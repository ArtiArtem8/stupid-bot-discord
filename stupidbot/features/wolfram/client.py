"""Bound Wolfram downloads and parse their XML into displayable results."""

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

from stupidbot import settings as config

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Self
    from xml.etree.ElementTree import Element


logger = logging.getLogger(__name__)
_PI_APPROXIMATION_RE = re.compile(r"(?<![\w.])3\.14159\d*(?![\w.])")


def format_math_text(text: str) -> str:
    """Replace decimal pi approximations and the word 'approx' with symbols."""
    text = _PI_APPROXIMATION_RE.sub("π", text)
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
        """Prefer plaintext, falling back to the image title."""
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


class WolframFailureReason(StrEnum):
    """Identify bot-generated fallback messages separately from external text."""

    NO_RESULTS = "no_results"
    UNKNOWN_API_ERROR = "unknown_api_error"
    INVALID_RESPONSE = "invalid_response"


@dataclass(frozen=True, slots=True)
class WolframResult:
    """The parsed response from the API."""

    success: bool
    pods: Sequence[Pod] = field(default_factory=tuple)
    error_msg: str | None = None
    failure_reason: WolframFailureReason | None = field(default=None, kw_only=True)

    @property
    def plot_url(self) -> str | None:
        """Extract the first valid plot URL."""
        for pod in self.pods:
            if _is_plot_pod(pod.id):
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
    return next((subpod.image_url for subpod in pod.subpods if subpod.image_url), None)


def _is_plot_pod(pod_id: str) -> bool:
    return "plot" in pod_id.lower() or pod_id == "ImagePod:GraphData"


def _parse_unsuccessful_result(root: Element) -> WolframResult:
    """Read the API error, with a fallback for absent or empty messages."""
    error = root.find("error")
    if error is None:
        return WolframResult(
            success=False,
            error_msg="No results found",
            failure_reason=WolframFailureReason.NO_RESULTS,
        )

    message = error.find("msg")
    error_msg = message.text.strip() if message is not None and message.text else ""
    return WolframResult(
        success=False,
        error_msg=error_msg or "Unknown API Error",
        failure_reason=None if error_msg else WolframFailureReason.UNKNOWN_API_ERROR,
    )


def _parse_subpod(element: Element) -> SubPod:
    plaintext = element.find("plaintext")
    image = element.find("img")
    return SubPod(
        plaintext=plaintext.text if plaintext is not None else None,
        image_url=image.get("src") if image is not None else None,
        image_title=image.get("title") if image is not None else None,
    )


def _should_ignore_pod(*, title: str, pod_id: str) -> bool:
    """Keep plot pods even when their titles match the exclusion list."""
    if _is_plot_pod(pod_id):
        return False
    return title in WOLFRAM_IGNORED_TITLES or any(
        pattern in title for pattern in WOLFRAM_IGNORED_PATTERNS
    )


def _has_displayable_content(subpods: Sequence[SubPod]) -> bool:
    return any(subpod.display_text or subpod.image_url for subpod in subpods)


def _parse_pod(element: Element) -> Pod | None:
    """Parse a pod with displayable content unless its title is excluded."""
    title = element.get("title", "")
    pod_id = element.get("id", "")
    if _should_ignore_pod(title=title, pod_id=pod_id):
        return None

    subpods = tuple(_parse_subpod(subpod) for subpod in element.findall("subpod"))
    if not _has_displayable_content(subpods):
        return None
    return Pod(title=title, id=pod_id, subpods=subpods)


async def _read_limited_response(
    response: aiohttp.ClientResponse,
    *,
    max_bytes: int,
    resource: str,
) -> bytes:
    """Read at most max_bytes, even if Content-Length is missing or incorrect."""
    if response.content_length is not None and response.content_length > max_bytes:
        raise WolframAPIError(f"{resource} exceeds the size limit")
    payload = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        if len(payload) + len(chunk) > max_bytes:
            raise WolframAPIError(f"{resource} exceeds the size limit")
        payload.extend(chunk)
    if not payload:
        raise WolframAPIError(f"{resource} is empty")
    return bytes(payload)


class WolframClient:
    """Query Wolfram v2 with a supplied or context-managed HTTP session."""

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
        """Create a session only when the caller has not supplied one."""
        if self._owns_session:
            self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *args: object) -> None:
        """Close the owned session; leave an injected session open."""
        if self._owns_session and self._session:
            await self._session.close()

    @property
    def session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            raise RuntimeError("Client session is not active.")
        return self._session

    async def query(self, input_str: str) -> WolframResult:
        """Download bounded XML and parse it off the event loop.

        Raises:
            WolframAPIError: The request failed or the response is empty or too large.
        """
        params = {
            "appid": self.app_id,
            "input": input_str,
            "format": "plaintext,image",
            "output": "xml",
            "excludepodid": "Identity",
            # Wolfram treats this as an approximate graphics width, not a guarantee.
            "plotwidth": str(config.WOLFRAM_PLOT_REQUEST_WIDTH),
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
                xml_data = await _read_limited_response(
                    resp,
                    max_bytes=config.WOLFRAM_XML_MAX_RESPONSE_BYTES,
                    resource="Wolfram response",
                )
            return await asyncio.to_thread(self._parse_xml, xml_data)
        except aiohttp.ClientResponseError as error:
            _raise_http_error(
                error,
                operation="query request",
                failure_message="Wolfram request failed",
            )
        except (TimeoutError, aiohttp.ClientError) as error:
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
                return await _read_limited_response(
                    response,
                    max_bytes=max_bytes,
                    resource="Plot download",
                )
        except aiohttp.ClientResponseError as error:
            _raise_http_error(
                error,
                operation="plot download",
                failure_message="Plot download failed",
            )
        except (TimeoutError, aiohttp.ClientError) as error:
            logger.warning("Wolfram plot download failed: %s", type(error).__name__)
            raise WolframAPIError("Plot download failed") from error

    def _parse_xml(self, xml_content: str | bytes) -> WolframResult:
        """Parse raw XML into structured dataclasses."""
        try:
            root = ET.fromstring(xml_content, forbid_dtd=True)
        except (ET.ParseError, DefusedXmlException, LookupError, ValueError):
            return WolframResult(
                success=False,
                error_msg="Invalid XML response",
                failure_reason=WolframFailureReason.INVALID_RESPONSE,
            )

        if root.get("success") != "true":
            return _parse_unsuccessful_result(root)

        pods = tuple(
            pod
            for element in root.findall("pod")
            if (pod := _parse_pod(element)) is not None
        )
        return WolframResult(success=True, pods=pods)


WOLFRAM_IGNORED_TITLES = frozenset(
    {
        "American pronunciation",
        "Anagrams",
        "Binary form",
        "Biological properties",
        "Comparisons",
        "Continued fraction",
        "Derivative",
        "Diagonalization",
        "Egyptian fraction expansion",
        "Expanded form",
        "Genome information",
        "Global minimum",
        "Image",
        "Implicit derivatives",
        "Input interpretation",
        "Integral representation",
        "Manipulatives illustration",
        "Mixed fraction",
        "Multiple-argument formula",
        "Number length",
        "Number name",
        "Other base conversions",
        "Overall typical frequency",
        "Periodicity",
        "Polynomial discriminant",
        "Possible closed forms",
        "Product of roots",
        "Properties",
        "Property",
        "Quotient and remainder",
        "Repeating decimal",
        "Scientific name",
        "Series expansion at x = 0",
        "Series representation",
        "Series representations",
        "Species authority",
        "Sum of roots",
        "Taxonomy",
        "Typical human computation times",
        "Values",
        "Visual representation",
        "Wikipedia summary",
    }
)

WOLFRAM_IGNORED_PATTERNS = frozenset(
    {
        "Additional",
        "All 2nd roots of",
        "Alternate form assuming",
        "Alternative",
        "Continued",
        "Difference",
        "Exchange history",
        "Percent decrease",
        "Percentage",
        "Polar",
        "Ratio with",
        "Reference triangle",
        "Riemann",
        "Series expansion at",
        "Vector",
    }
)
