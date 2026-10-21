from __future__ import annotations

import asyncio
import unittest
from collections.abc import AsyncIterator
from threading import get_ident
from typing import Any, Self, override
from unittest.mock import MagicMock, patch

import aiohttp

from stupidbot import settings as config
from stupidbot.features.wolfram.client import (
    Pod,
    SubPod,
    WolframAPIError,
    WolframClient,
    WolframRateLimitError,
    WolframResult,
    format_math_text,
)

_MINIMAL_PLOT_XML = (
    '<queryresult success="true" error="false">'
    '<pod title="Plots" id="Plot">'
    "<subpod><plaintext>synthetic plot</plaintext>"
    '<img src="https://example.invalid/wolfram-plot.gif" '
    'title="synthetic plot" /></subpod></pod></queryresult>'
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
        error: BaseException | None = None,
    ) -> None:
        self.content = _ContentStream(chunks)
        self.content_length = content_length
        self._error = error

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def raise_for_status(self) -> None:
        if self._error:
            raise self._error


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.closed = False
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append((url, kwargs))
        return self.response


def _client_response_error(status: int) -> aiohttp.ClientResponseError:
    return aiohttp.ClientResponseError(
        request_info=MagicMock(),
        history=(),
        status=status,
    )


class TestWolframParsing(unittest.TestCase):
    @override
    def setUp(self) -> None:
        session: Any = _Session(_Response())
        self.client = WolframClient("test-app-id", session=session)

    def test_minimal_plot_xml_is_parsed(self) -> None:
        result = self.client._parse_xml(_MINIMAL_PLOT_XML)

        self.assertTrue(result.success)
        self.assertEqual([pod.id for pod in result.pods], ["Plot"])
        self.assertEqual(
            result.plot_url,
            "https://example.invalid/wolfram-plot.gif",
        )

    def test_format_math_text(self) -> None:
        cases = (
            (
                "formats standalone approximations globally",
                "(3.14159), -3.1415926535; x approx y approx z",
                "(π), -π; x ≈ y ≈ z",
            ),
            (
                "preserves near misses and embedded values",
                "13.14159 3.1415 3x14159 x3.14159 3.14159rad 3.14159e10 3.14159.2",
                "13.14159 3.1415 3x14159 x3.14159 3.14159rad 3.14159e10 3.14159.2",
            ),
            (
                "only replaces approx surrounded by spaces",
                "approx x; x approx; x\tapprox\ty; x  approx  y",
                "approx x; x approx; x\tapprox\ty; x  ≈  y",
            ),
        )

        for description, text, expected in cases:
            with self.subTest(description=description):
                self.assertEqual(format_math_text(text), expected)

    def _assert_invalid_xml(self, xml: str | bytes) -> None:
        result = self.client._parse_xml(xml)

        self.assertFalse(result.success)
        self.assertEqual(result.error_msg, "Invalid XML response")

    def test_unsafe_or_malformed_xml_is_rejected(self) -> None:
        cases = (
            ("malformed", '<queryresult success="true"><pod>'),
            ("doctype", '<!DOCTYPE queryresult><queryresult success="true" />'),
            (
                "entity declaration",
                "<!DOCTYPE queryresult [<!ENTITY injected 'unsafe'>]>"
                '<queryresult success="true">&injected;</queryresult>',
            ),
            (
                "unknown byte encoding",
                b'<?xml version="1.0" encoding="unknown"?><queryresult/>',
            ),
            (
                "unsupported byte encoding",
                b'<?xml version="1.0" encoding="utf-32"?><queryresult/>',
            ),
            (
                "malformed encoded text",
                b'<?xml version="1.0" encoding="utf-8"?>'
                b"<queryresult>\xff</queryresult>",
            ),
        )

        for description, xml in cases:
            with self.subTest(description=description):
                self._assert_invalid_xml(xml)

    def test_unsuccessful_response_reports_best_available_error(self) -> None:
        cases = (
            (
                "no error element",
                '<queryresult success="false" />',
                "No results found",
            ),
            (
                "error message",
                """
                <queryresult success="false">
                  <error><msg>  Invalid input
                  </msg></error>
                </queryresult>
                """,
                "Invalid input",
            ),
            (
                "missing message element",
                """
                <queryresult success="false">
                  <error />
                </queryresult>
                """,
                "Unknown API Error",
            ),
            (
                "empty message element",
                """
                <queryresult success="false">
                  <error><msg /></error>
                </queryresult>
                """,
                "Unknown API Error",
            ),
            (
                "blank message",
                """
                <queryresult success="false">
                  <error><msg>   </msg></error>
                </queryresult>
                """,
                "Unknown API Error",
            ),
        )

        for description, xml, expected in cases:
            with self.subTest(description=description):
                result = self.client._parse_xml(xml)

                self.assertFalse(result.success)
                self.assertEqual(result.error_msg, expected)

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

    def test_plot_url_falls_back_to_graph_title(self) -> None:
        result = WolframResult(
            success=True,
            pods=(
                Pod(
                    title="Graph visualization",
                    id="Properties",
                    subpods=(
                        SubPod(
                            plaintext="metadata",
                            image_url=None,
                            image_title=None,
                        ),
                        SubPod(
                            plaintext=None,
                            image_url="https://example.invalid/graph.gif",
                            image_title=None,
                        ),
                    ),
                ),
            ),
        )

        self.assertEqual(result.plot_url, "https://example.invalid/graph.gif")

    def test_parsed_pod_joins_best_available_text(self) -> None:
        result = self.client._parse_xml(
            """
            <queryresult success="true">
              <pod title="Result" id="Result">
                <subpod>
                  <plaintext>3.14159</plaintext>
                  <img title="ignored image title" />
                </subpod>
                <subpod>
                  <img title="x approx y" />
                </subpod>
                <subpod>
                  <img src="https://example.invalid/result.gif" />
                </subpod>
              </pod>
            </queryresult>
            """
        )

        self.assertEqual(len(result.pods), 1)
        self.assertEqual(result.pods[0].get_joined_text(), "π\nx ≈ y")
        self.assertTrue(result.pods[0].is_primary)

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

    def test_plot_id_bypasses_ignored_title_pattern(self) -> None:
        result = self.client._parse_xml(
            """
            <queryresult success="true">
              <pod title="Polar plot" id="Properties">
                <subpod><plaintext>ignored</plaintext></subpod>
              </pod>
              <pod title="Polar plot" id="PolarPlot">
                <subpod><img src="https://example.invalid/polar.gif" /></subpod>
              </pod>
            </queryresult>
            """
        )

        self.assertEqual([pod.id for pod in result.pods], ["PolarPlot"])
        self.assertEqual(result.plot_url, "https://example.invalid/polar.gif")

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

    async def test_query_builds_expected_request(self) -> None:
        response = _Response(chunks=(b'<queryresult success="false"/>',))
        client, session = self._client(response)

        with patch.object(config, "WOLFRAM_HTTP_TIMEOUT_SECONDS", 17):
            await client.query("plot sin(x)")

        self.assertEqual(len(session.calls), 1)
        url, arguments = session.calls[0]
        self.assertEqual(url, "https://api.wolframalpha.com/v2/query")
        self.assertEqual(set(arguments), {"params", "timeout"})
        self.assertEqual(config.WOLFRAM_PLOT_REQUEST_WIDTH, 400)
        self.assertEqual(
            arguments["params"],
            {
                "appid": "test-app-id",
                "input": "plot sin(x)",
                "format": "plaintext,image",
                "output": "xml",
                "excludepodid": "Identity",
                "plotwidth": "400",
            },
        )
        timeout = arguments["timeout"]
        self.assertIsInstance(timeout, aiohttp.ClientTimeout)
        self.assertEqual(timeout.total, 17)

    async def test_query_rejects_declared_oversize_before_reading_or_parsing(
        self,
    ) -> None:
        response = _Response(chunks=(b"unread body",), content_length=33)
        client, _ = self._client(response)
        with (
            patch.object(config, "WOLFRAM_XML_MAX_RESPONSE_BYTES", 32),
            patch.object(client, "_parse_xml", wraps=client._parse_xml) as parse,
            self.assertRaisesRegex(
                WolframAPIError, "^Wolfram response exceeds the size limit$"
            ),
        ):
            await client.query("solve x")
        self.assertFalse(response.content.iterated)
        parse.assert_not_called()

    async def test_query_limits_streamed_bytes_even_with_understated_length(
        self,
    ) -> None:
        for declared_length in (None, 1):
            with self.subTest(content_length=declared_length):
                response = _Response(
                    chunks=(b"a" * 20, b"b" * 13, AssertionError("read after limit")),
                    content_length=declared_length,
                )
                client, _ = self._client(response)
                with (
                    patch.object(config, "WOLFRAM_XML_MAX_RESPONSE_BYTES", 32),
                    patch.object(
                        client, "_parse_xml", wraps=client._parse_xml
                    ) as parse,
                    self.assertRaisesRegex(WolframAPIError, "exceeds the size limit"),
                ):
                    await client.query("solve x")
                parse.assert_not_called()

    async def test_query_accepts_valid_xml_at_the_exact_byte_limit(self) -> None:
        xml = b'<queryresult success="true"/>'
        for declared_length in (None, len(xml)):
            with self.subTest(content_length=declared_length):
                response = _Response(
                    chunks=(xml[:10], xml[10:]), content_length=declared_length
                )
                client, _ = self._client(response)
                with patch.object(config, "WOLFRAM_XML_MAX_RESPONSE_BYTES", len(xml)):
                    result = await client.query("solve x")
                self.assertTrue(result.success)

    async def test_query_rejects_empty_xml_before_parsing(self) -> None:
        client, _ = self._client(_Response())
        with (
            patch.object(client, "_parse_xml", wraps=client._parse_xml) as parse,
            self.assertRaisesRegex(WolframAPIError, "^Wolfram response is empty$"),
        ):
            await client.query("solve x")
        parse.assert_not_called()

    async def test_query_parses_declared_encoding_in_a_worker_thread(self) -> None:
        xml = (
            b'<?xml version="1.0" encoding="iso-8859-1"?>'
            b'<queryresult success="true"><pod title="Result" id="Result">'
            b"<subpod><plaintext>caf\xe9</plaintext></subpod></pod></queryresult>"
        )
        client, _ = self._client(_Response(chunks=(xml,)))
        parse = client._parse_xml
        parser_threads: list[int] = []

        def parse_in_worker(payload: str | bytes) -> WolframResult:
            parser_threads.append(get_ident())
            return parse(payload)

        with patch.object(client, "_parse_xml", side_effect=parse_in_worker):
            result = await client.query("solve x")
        self.assertEqual(result.pods[0].get_joined_text(), "caf\u00e9")
        self.assertEqual(len(parser_threads), 1)
        self.assertNotEqual(parser_threads[0], get_ident())

    async def test_query_cancellation_during_body_read_does_not_parse(self) -> None:
        client, _ = self._client(_Response(chunks=(asyncio.CancelledError(),)))
        with (
            patch.object(client, "_parse_xml", wraps=client._parse_xml) as parse,
            self.assertRaises(asyncio.CancelledError),
        ):
            await client.query("solve x")
        parse.assert_not_called()

    async def test_query_preserves_safe_xml_validation_through_http(self) -> None:
        for xml in (
            b'<queryresult success="true"><pod>',
            b'<!DOCTYPE queryresult><queryresult success="true"/>',
            b'<!DOCTYPE queryresult [<!ENTITY unsafe "value">]>'
            b'<queryresult success="true">&unsafe;</queryresult>',
        ):
            with self.subTest(xml=xml):
                client, _ = self._client(_Response(chunks=(xml,)))
                result = await client.query("solve x")
                self.assertFalse(result.success)
                self.assertEqual(result.error_msg, "Invalid XML response")

    async def test_query_failure_logs_exclude_sensitive_request_details(self) -> None:
        private_url = "https://example.invalid/?appid=private-key&input=private-query"
        for error in (
            aiohttp.ClientResponseError(
                request_info=MagicMock(real_url=private_url), history=(), status=429
            ),
            aiohttp.ClientResponseError(
                request_info=MagicMock(real_url=private_url), history=(), status=500
            ),
            aiohttp.ClientError(private_url),
            TimeoutError(private_url),
        ):
            with self.subTest(
                error=type(error).__name__, status=getattr(error, "status", None)
            ):
                client, _ = self._client(_Response(error=error))
                with (
                    self.assertLogs(
                        "stupidbot.features.wolfram.client", level="WARNING"
                    ) as logs,
                    self.assertRaises(WolframAPIError),
                ):
                    await client.query("private-query")
                self.assertEqual(len(logs.records), 1)
                self.assertNotIn("private-key", logs.output[0])
                self.assertNotIn("private-query", logs.output[0])
                self.assertIsNone(logs.records[0].exc_info)

    async def test_query_429_has_specific_error_without_retry(self) -> None:
        client, session = self._client(_Response(error=_client_response_error(429)))

        with self.assertRaisesRegex(WolframRateLimitError, "try again"):
            await client.query("plot sin(x)")

        self.assertEqual(len(session.calls), 1)

    async def test_query_non_rate_limit_http_error_is_wrapped(self) -> None:
        client, _ = self._client(_Response(error=_client_response_error(500)))

        with self.assertRaisesRegex(
            WolframAPIError,
            "^Wolfram request failed$",
        ):
            await client.query("plot sin(x)")

    async def test_query_client_error_is_wrapped(self) -> None:
        client, _ = self._client(
            _Response(error=aiohttp.ClientError("connection failed"))
        )

        with self.assertRaisesRegex(
            WolframAPIError,
            "^Wolfram request failed$",
        ):
            await client.query("plot sin(x)")

    async def test_successful_chunked_download(self) -> None:
        response = _Response(chunks=(b"abc", b"def"))
        client, _ = self._client(response)

        payload = await client.fetch_plot_image(
            "https://example.invalid/plot", max_bytes=6
        )

        self.assertEqual(payload, b"abcdef")
        self.assertEqual(response.content.requested_chunk_size, 64 * 1024)

    async def test_source_larger_than_legacy_upload_limit_is_allowed(self) -> None:
        response = _Response(chunks=(b"a" * 10, b"b" * 10))
        client, _ = self._client(response)

        payload = await client.fetch_plot_image(
            "https://example.invalid/plot", max_bytes=32
        )

        self.assertEqual(payload, b"a" * 10 + b"b" * 10)

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

        self.assertEqual(payload, b"a" * 16 + b"b" * 16)

    async def test_non_positive_download_limit_is_rejected_before_request(
        self,
    ) -> None:
        client, session = self._client(_Response())

        with self.assertRaisesRegex(
            WolframAPIError,
            "^Plot download limit must be positive$",
        ):
            await client.fetch_plot_image(
                "https://example.invalid/plot",
                max_bytes=0,
            )

        self.assertEqual(session.calls, [])

    async def test_empty_payload_is_rejected(self) -> None:
        client, _ = self._client(_Response())

        with self.assertRaisesRegex(WolframAPIError, "empty"):
            await client.fetch_plot_image("https://example.invalid/plot", max_bytes=32)

    async def test_plot_download_failures_are_wrapped(self) -> None:
        cases = (
            (
                "HTTP response error",
                _client_response_error(500),
            ),
            (
                "client error",
                aiohttp.ClientError("connection failed"),
            ),
            (
                "timeout",
                TimeoutError(),
            ),
        )

        for description, error in cases:
            with self.subTest(description=description):
                client, _ = self._client(_Response(error=error))

                with self.assertRaisesRegex(
                    WolframAPIError,
                    "^Plot download failed$",
                ):
                    await client.fetch_plot_image(
                        "https://example.invalid/plot",
                        max_bytes=32,
                    )

    async def test_plot_download_429_has_specific_error_without_retry(self) -> None:
        client, session = self._client(_Response(error=_client_response_error(429)))

        with self.assertRaisesRegex(WolframRateLimitError, "try again"):
            await client.fetch_plot_image(
                "https://example.invalid/plot",
                max_bytes=32,
            )

        self.assertEqual(len(session.calls), 1)

    async def test_cancellation_propagates(self) -> None:
        response = _Response(chunks=(asyncio.CancelledError(),))
        client, _ = self._client(response)

        with self.assertRaises(asyncio.CancelledError):
            await client.fetch_plot_image("https://example.invalid/plot", max_bytes=32)
