"""Wolfram Alpha API Client."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Final, Never

import aiohttp
from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException

import config
from resources import WOLFRAM_IGNORED_PATTERNS, WOLFRAM_IGNORED_TITLES

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Self
    from xml.etree.ElementTree import Element


logger = logging.getLogger(__name__)


class PodType(StrEnum):
    """Standard Wolfram Pod IDs for logic routing."""

    INPUT = "Input"
    RESULT = "Result"
    SOLUTION = "Solution"
    PLOT = "Plot"


def format_math_text(text: str) -> str:
    """Format mathematical text for better readability."""
    text = re.sub(r"3\.14159\d+", "π", text)
    text = text.replace(" approx ", " ≈ ")
    return text


@dataclass(frozen=True, slots=True)
class SubPod:
    """A single entry within a result pod."""

    plaintext: str | None
    image_url: str | None
    image_title: str | None

    @property
    def display_text(self) -> str | None:
        """Return the best text representation."""
        return self.plaintext or self.image_title


@dataclass(frozen=True, slots=True)
class Pod:
    """A container for results (e.g., 'Input', 'Result', 'Plot')."""

    title: str
    id: str
    subpods: Sequence[SubPod]

    @property
    def is_primary(self) -> bool:
        """Check if this pod contains the main answer."""
        return (
            self.title in ("Result", "Solutions", "Exact result")
            or "result" in self.id.lower()
        )

    def get_joined_text(self) -> str:
        """Get all text results combined."""
        texts = [s.display_text for s in self.subpods if s.display_text]
        return format_math_text("\n".join(texts))


@dataclass(frozen=True, slots=True)
class WolframResult:
    """The parsed response from the API."""

    success: bool
    pods: Sequence[Pod] = field(default_factory=tuple)
    error_msg: str | None = None

    @property
    def plot_url(self) -> str | None:
        """Extract the first valid plot URL."""
        for pod in self.pods:
            if "plot" in pod.id.lower() or pod.id == "ImagePod:GraphData":
                if image_url := _first_image_url(pod):
                    return image_url
        for pod in self.pods:
            if "graph" in pod.title.lower():
                if image_url := _first_image_url(pod):
                    return image_url
        return None


class WolframAPIError(Exception):
    """Base exception for API failures."""


class WolframRateLimitError(WolframAPIError):
    """Raised when Wolfram rejects a request due to rate limiting."""


_RATE_LIMIT_MESSAGE = "Wolfram is busy. Please try again in a few seconds."


def _raise_http_error(
    error: aiohttp.ClientResponseError,
    *,
    operation: str,
    failure_message: str,
) -> Never:
    """Convert an HTTP response failure without exposing its request URL."""
    if error.status == 429:
        logger.warning("Wolfram %s was rate limited", operation)
        raise WolframRateLimitError(_RATE_LIMIT_MESSAGE) from error
    logger.warning("Wolfram %s failed with HTTP %s", operation, error.status)
    raise WolframAPIError(failure_message) from error


def _first_image_url(pod: Pod) -> str | None:
    """Return the first image URL in a pod."""
    return next((subpod.image_url for subpod in pod.subpods if subpod.image_url), None)


def _parse_unsuccessful_result(root: Element) -> WolframResult:
    """Build the existing failure result from an unsuccessful response."""
    error = root.find("error")
    if error is None:
        return WolframResult(success=False, error_msg="No results found")
    message = error.find("msg")
    return WolframResult(
        success=False,
        error_msg=message.text if message is not None else "Unknown API Error",
    )


def _parse_subpod(element: Element) -> SubPod:
    """Parse one Wolfram subpod."""
    plaintext = element.find("plaintext")
    image = element.find("img")
    return SubPod(
        plaintext=plaintext.text if plaintext is not None else None,
        image_url=image.get("src") if image is not None else None,
        image_title=image.get("title") if image is not None else None,
    )


def _should_ignore_pod(*, title: str, pod_id: str) -> bool:
    """Return whether the existing pod filters should exclude a pod."""
    if pod_id == "ImagePod:GraphData":
        return False
    return title in WOLFRAM_IGNORED_TITLES or any(
        pattern in title for pattern in WOLFRAM_IGNORED_PATTERNS
    )


def _has_displayable_content(subpods: Sequence[SubPod]) -> bool:
    """Return whether at least one subpod has text or an image URL."""
    return any(subpod.display_text or subpod.image_url for subpod in subpods)


def _parse_pod(element: Element) -> Pod | None:
    """Parse one displayable pod while preserving the existing filters."""
    title = element.get("title", "")
    pod_id = element.get("id", "")
    if _should_ignore_pod(title=title, pod_id=pod_id):
        return None

    subpods = tuple(_parse_subpod(subpod) for subpod in element.findall("subpod"))
    if not _has_displayable_content(subpods):
        return None
    return Pod(title=title, id=pod_id, subpods=subpods)


def _validate_content_length(
    content_length: int | None,
    *,
    max_bytes: int,
) -> None:
    """Reject a declared plot size before its response body is read."""
    if content_length is not None and content_length > max_bytes:
        raise WolframAPIError("Plot download exceeds the size limit")


async def _read_limited_response(
    response: aiohttp.ClientResponse,
    *,
    max_bytes: int,
) -> bytes:
    """Read a plot response while enforcing its streaming byte limit."""
    payload = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        payload.extend(chunk)
        if len(payload) > max_bytes:
            raise WolframAPIError("Plot download exceeds the size limit")
    if not payload:
        raise WolframAPIError("Plot download is empty")
    return bytes(payload)


class WolframClient:
    """Async HTTP client for Wolfram Alpha v2 API."""

    def __init__(
        self, app_id: str | None, session: aiohttp.ClientSession | None = None
    ) -> None:
        if not app_id:
            raise ValueError(
                "Wolfram Alpha app_id is required and cannot be None or empty"
            )
        self.app_id: Final[str] = app_id
        self._session = session
        self._owns_session: Final[bool] = session is None

    async def __aenter__(self) -> Self:
        """Enter async context manager."""
        if self._owns_session:
            self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *args: object) -> None:
        """Exit async context manager and cleanup resources."""
        if self._owns_session and self._session:
            await self._session.close()

    @property
    def session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            raise RuntimeError("Client session is not active.")
        return self._session

    async def query(self, input_str: str) -> WolframResult:
        """Execute a query and return parsed results.

        Raises:
            WolframAPIError: On network or parsing failure.

        """
        params = {
            "appid": self.app_id,
            "input": input_str,
            "format": "plaintext,image",
            "output": "xml",
            "excludepodid": "Identity",
            # Wolfram treats this as an approximate graphics width, not a guarantee.
            "plotwidth": str(config.WOLFRAM_PLOT_TARGET_WIDTH),
        }

        try:
            async with self.session.get(
                config.WOLFRAM_API_URL,
                params=params,
                timeout=aiohttp.ClientTimeout(
                    total=config.WOLFRAM_HTTP_TIMEOUT_SECONDS
                ),
            ) as resp:
                resp.raise_for_status()
                xml_data = await resp.text()
                return self._parse_xml(xml_data)
        except aiohttp.ClientResponseError as error:
            _raise_http_error(
                error,
                operation="query request",
                failure_message="Wolfram request failed",
            )
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            logger.warning("Wolfram query request failed: %s", type(error).__name__)
            raise WolframAPIError("Wolfram request failed") from error

    async def fetch_plot_image(self, url: str, *, max_bytes: int) -> bytes:
        """Download a plot through the persistent session with a hard byte limit."""
        if max_bytes <= 0:
            raise WolframAPIError("Plot download limit must be positive")

        try:
            timeout = aiohttp.ClientTimeout(total=config.WOLFRAM_HTTP_TIMEOUT_SECONDS)
            async with self.session.get(url, timeout=timeout) as response:
                response.raise_for_status()
                _validate_content_length(
                    response.content_length,
                    max_bytes=max_bytes,
                )
                return await _read_limited_response(response, max_bytes=max_bytes)
        except aiohttp.ClientResponseError as error:
            _raise_http_error(
                error,
                operation="plot download",
                failure_message="Plot download failed",
            )
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            logger.warning("Wolfram plot download failed: %s", type(error).__name__)
            raise WolframAPIError("Plot download failed") from error

    def _parse_xml(self, xml_content: str) -> WolframResult:
        """Parses raw XML into structured dataclasses."""
        try:
            root = ET.fromstring(xml_content, forbid_dtd=True)
        except (ET.ParseError, DefusedXmlException):
            return WolframResult(success=False, error_msg="Invalid XML response")

        if root.get("success") != "true":
            return _parse_unsuccessful_result(root)

        pods = tuple(
            pod
            for element in root.findall("pod")
            if (pod := _parse_pod(element)) is not None
        )
        return WolframResult(success=True, pods=pods)
