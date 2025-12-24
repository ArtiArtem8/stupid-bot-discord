"""Wolfram Alpha API Client."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Final

import aiohttp
from aiohttp.client import DEFAULT_TIMEOUT
from defusedxml import ElementTree as ET

import config
from resources import WOLFRAM_IGNORED_PATTERNS, WOLFRAM_IGNORED_TITLES

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Self


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
            if "plot" in pod.id.lower() or "graph" in pod.title.lower():
                for sub in pod.subpods:
                    if sub.image_url:
                        return sub.image_url
        return None


class WolframAPIError(Exception):
    """Base exception for API failures."""


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
        }

        try:
            async with self.session.get(
                config.WOLFRAM_API_URL, params=params, timeout=DEFAULT_TIMEOUT
            ) as resp:
                resp.raise_for_status()
                xml_data = await resp.text()
                return self._parse_xml(xml_data)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error("Wolfram network error: %s", e)
            raise WolframAPIError(f"Network error: {e}") from e
        except Exception as e:
            logger.error("Wolfram query error: %s", e, exc_info=True)
            raise WolframAPIError(f"Processing error: {e}") from e

    def _parse_xml(self, xml_content: str) -> WolframResult:
        """Parses raw XML into structured dataclasses."""
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError:
            return WolframResult(success=False, error_msg="Invalid XML response")

        if root.get("success") != "true":
            # Try to find error message
            if (err_node := root.find("error")) is not None:
                msg = err_node.find("msg")
                return WolframResult(
                    success=False,
                    error_msg=msg.text if msg is not None else "Unknown API Error",
                )
            return WolframResult(success=False, error_msg="No results found")

        pods: list[Pod] = []

        for pod_elem in root.findall("pod"):
            title = pod_elem.get("title", "")
            pod_id = pod_elem.get("id", "")

            # Filtering
            if title in WOLFRAM_IGNORED_TITLES:
                continue
            if any(pat in title for pat in WOLFRAM_IGNORED_PATTERNS):
                continue

            subpods: list[SubPod] = []
            for sub_elem in pod_elem.findall("subpod"):
                plaintext = sub_elem.find("plaintext")
                img = sub_elem.find("img")

                subpods.append(
                    SubPod(
                        plaintext=plaintext.text if plaintext is not None else None,
                        image_url=img.get("src") if img is not None else None,
                        image_title=img.get("title") if img is not None else None,
                    )
                )

            # Only add pods that have actual content
            if subpods and any(s.display_text or s.image_url for s in subpods):
                pods.append(Pod(title=title, id=pod_id, subpods=tuple(subpods)))

        return WolframResult(success=True, pods=tuple(pods))
