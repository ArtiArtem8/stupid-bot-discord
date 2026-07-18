from __future__ import annotations

import asyncio
import unittest
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, override
from unittest.mock import MagicMock, patch

import aiohttp

import config
from api.wolfram import WolframAPIError, WolframClient, WolframRateLimitError

WOLFRAM_XML_FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "wolfram" / "minimal_plot.xml"
)


class _ContentStream:
    def __init__(self, chunks: tuple[bytes | BaseException, ...]) -> None:
        self.chunks = chunks
        self.iterated = False
        self.requested_chunk_size: int | None = None

    async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        self.iterated = True
        self.requested_chunk_size = size
        for chunk in self.chunks:
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk


class _Response:
    def __init__(
        self,
        *,
        chunks: tuple[bytes | BaseException, ...] = (),
        content_length: int | None = None,
        text: str = "",
        error: BaseException | None = None,
    ) -> None:
        self.content = _ContentStream(chunks)
        self.content_length = content_length
        self._text = text
        self._error = error

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def raise_for_status(self) -> None:
        if self._error:
            raise self._error

    async def text(self) -> str:
        return self._text


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.closed = False
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append((url, kwargs))
        return self.response


class TestWolframParsing(unittest.TestCase):
    @override
    def setUp(self) -> None:
        session: Any = _Session(_Response())
        self.client = WolframClient("test-app-id", session=session)

    def test_parse_synthetic_xml_fixture(self) -> None:
        result = self.client._parse_xml(WOLFRAM_XML_FIXTURE.read_text(encoding="utf-8"))

        self.assertTrue(result.success)
        self.assertEqual([pod.id for pod in result.pods], ["Plot"])

    def test_plot_url_is_extracted_from_synthetic_response(self) -> None:
        result = self.client._parse_xml(WOLFRAM_XML_FIXTURE.read_text(encoding="utf-8"))

        plot_url = result.plot_url
        if plot_url is None:
            self.fail("synthetic Wolfram fixture did not expose a plot URL")
        self.assertTrue(plot_url.startswith(("http://", "https://")))
        self.assertNotIn("appid", plot_url.lower())

    def _assert_invalid_xml(self, xml: str) -> None:
        result = self.client._parse_xml(xml)

        self.assertFalse(result.success)
        self.assertEqual(result.error_msg, "Invalid XML response")

    def test_malformed_xml_is_rejected_safely(self) -> None:
        self._assert_invalid_xml('<queryresult success="true"><pod>')

    def test_doctype_xml_is_rejected_safely(self) -> None:
        self._assert_invalid_xml('<!DOCTYPE queryresult><queryresult success="true" />')

    def test_entity_declaration_is_rejected_safely(self) -> None:
        self._assert_invalid_xml(
            "<!DOCTYPE queryresult [<!ENTITY injected 'unsafe'>]>"
            + '<queryresult success="true">&injected;</queryresult>'
        )

    def test_wrong_input_response_is_unsuccessful(self) -> None:
        result = self.client._parse_xml(
            '<queryresult success="false" error="false" numpods="0" />'
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_msg, "No results found")

    def test_complete_graph_prefers_image_pod_over_graph_features(self) -> None:
        result = self.client._parse_xml(
            """
            <queryresult success="true">
              <pod title="Graph features" id="PropertiesPod:GraphData">
                <subpod><img src="https://example.invalid/features.gif" /></subpod>
              </pod>
              <pod title="Image" id="ImagePod:GraphData">
                <subpod><img src="https://example.invalid/graph.gif" /></subpod>
              </pod>
            </queryresult>
            """
        )

        self.assertEqual(result.plot_url, "https://example.invalid/graph.gif")

    def test_synthetic_3d_plot_url_is_extracted(self) -> None:
        result = self.client._parse_xml(
            """
            <queryresult success="true">
              <pod title="3D plot" id="3DPlot">
                <subpod><img src="https://example.invalid/3d-plot.gif" /></subpod>
              </pod>
            </queryresult>
            """
        )

        self.assertEqual(result.plot_url, "https://example.invalid/3d-plot.gif")

    def test_ignored_pod_is_not_added(self) -> None:
        result = self.client._parse_xml(
            """
            <queryresult success="true">
              <pod title="Properties" id="Properties">
                <subpod><plaintext>ignored</plaintext></subpod>
              </pod>
            </queryresult>
            """
        )

        self.assertEqual(result.pods, ())

    def test_graph_image_pod_bypasses_ignored_title(self) -> None:
        result = self.client._parse_xml(
            """
            <queryresult success="true">
              <pod title="Properties" id="ImagePod:GraphData">
                <subpod><img src="https://example.invalid/graph.gif" /></subpod>
              </pod>
            </queryresult>
            """
        )

        self.assertEqual([pod.id for pod in result.pods], ["ImagePod:GraphData"])

    def test_empty_pod_is_not_added(self) -> None:
        result = self.client._parse_xml(
            """
            <queryresult success="true">
              <pod title="Result" id="Result"><subpod /></pod>
            </queryresult>
            """
        )

        self.assertEqual(result.pods, ())


class TestWolframHTTP(unittest.IsolatedAsyncioTestCase):
    def _client(self, response: _Response) -> tuple[WolframClient, _Session]:
        session = _Session(response)
        session_argument: Any = session
        return WolframClient("test-app-id", session=session_argument), session

    async def test_query_uses_configured_timeout(self) -> None:
        response = _Response(text='<queryresult success="false"/>')
        client, session = self._client(response)

        with patch.object(config, "WOLFRAM_HTTP_TIMEOUT_SECONDS", 17):
            await client.query("plot sin(x)")

        timeout = session.calls[0][1]["timeout"]
        self.assertIsInstance(timeout, aiohttp.ClientTimeout)
        self.assertEqual(timeout.total, 17)

    async def test_query_uses_https_endpoint(self) -> None:
        response = _Response(text='<queryresult success="false"/>')
        client, session = self._client(response)

        await client.query("plot sin(x)")

        self.assertEqual(session.calls[0][0], "https://api.wolframalpha.com/v2/query")

    async def test_query_requests_approximate_target_plot_width(self) -> None:
        response = _Response(text='<queryresult success="false"/>')
        client, session = self._client(response)

        await client.query("plot sin(x)")

        params = session.calls[0][1]["params"]
        self.assertEqual(params["plotwidth"], str(config.WOLFRAM_PLOT_TARGET_WIDTH))

    async def test_query_429_has_specific_error_without_retry(self) -> None:
        error = aiohttp.ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=429,
        )
        client, session = self._client(_Response(error=error))

        with self.assertRaisesRegex(WolframRateLimitError, "try again"):
            await client.query("plot sin(x)")

        self.assertEqual(len(session.calls), 1)

    async def test_successful_chunked_download(self) -> None:
        response = _Response(chunks=(b"abc", b"def"))
        client, _ = self._client(response)

        payload = await client.fetch_plot_image(
            "https://example.invalid/plot", max_bytes=6
        )

        self.assertEqual(payload, b"abcdef")
        self.assertIsInstance(payload, bytes)
        self.assertEqual(response.content.requested_chunk_size, 64 * 1024)

    async def test_source_larger_than_legacy_upload_limit_is_allowed(self) -> None:
        response = _Response(chunks=(b"a" * 10, b"b" * 10))
        client, _ = self._client(response)

        payload = await client.fetch_plot_image(
            "https://example.invalid/plot", max_bytes=32
        )

        self.assertGreater(len(payload), 16)
        self.assertEqual(len(payload), 20)

    async def test_known_content_length_over_limit_stops_before_body(self) -> None:
        response = _Response(chunks=(b"body",), content_length=33)
        client, _ = self._client(response)

        with self.assertRaises(WolframAPIError):
            await client.fetch_plot_image("https://example.invalid/plot", max_bytes=32)

        self.assertFalse(response.content.iterated)

    async def test_stream_crossing_limit_without_content_length_is_rejected(
        self,
    ) -> None:
        response = _Response(chunks=(b"a" * 20, b"b" * 13))
        client, _ = self._client(response)

        with self.assertRaises(WolframAPIError):
            await client.fetch_plot_image("https://example.invalid/plot", max_bytes=32)

    async def test_payload_exactly_at_limit_is_allowed(self) -> None:
        response = _Response(chunks=(b"a" * 16, b"b" * 16), content_length=32)
        client, _ = self._client(response)

        payload = await client.fetch_plot_image(
            "https://example.invalid/plot", max_bytes=32
        )

        self.assertEqual(len(payload), 32)

    async def test_empty_payload_is_rejected(self) -> None:
        client, _ = self._client(_Response())

        with self.assertRaisesRegex(WolframAPIError, "empty"):
            await client.fetch_plot_image("https://example.invalid/plot", max_bytes=32)

    async def test_http_error_is_wrapped(self) -> None:
        response = _Response(error=aiohttp.ClientError("HTTP 500"))
        client, _ = self._client(response)

        with self.assertRaisesRegex(WolframAPIError, "download failed"):
            await client.fetch_plot_image("https://example.invalid/plot", max_bytes=32)

    async def test_plot_download_429_has_specific_error_without_retry(self) -> None:
        error = aiohttp.ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=429,
        )
        client, session = self._client(_Response(error=error))

        with self.assertRaisesRegex(WolframRateLimitError, "try again"):
            await client.fetch_plot_image(
                "https://example.invalid/plot",
                max_bytes=32,
            )

        self.assertEqual(len(session.calls), 1)

    async def test_timeout_is_wrapped(self) -> None:
        response = _Response(error=asyncio.TimeoutError())
        client, _ = self._client(response)

        with self.assertRaisesRegex(WolframAPIError, "download failed"):
            await client.fetch_plot_image("https://example.invalid/plot", max_bytes=32)

    async def test_cancellation_propagates(self) -> None:
        response = _Response(chunks=(asyncio.CancelledError(),))
        client, _ = self._client(response)

        with self.assertRaises(asyncio.CancelledError):
            await client.fetch_plot_image("https://example.invalid/plot", max_bytes=32)
