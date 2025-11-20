"""Wolfram Alpha integration for mathematical problem solving.

Features:
- Asynchronous HTTP requests via aiohttp
- Strict XML parsing for stability
- Specialized mathematical output formatting
- Image optimization pipeline
- Type-safe architecture (Python 3.12+)

Requirements:
    WOLFRAM_APP_ID environment variable must be set
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from textwrap import shorten
from typing import TYPE_CHECKING, Final, Literal, Self, override

import aiohttp
import discord
from aiohttp.client import DEFAULT_TIMEOUT
from defusedxml import ElementTree as ET
from discord import File, Interaction, app_commands
from discord.ext import commands

import config
from resources import WOLFRAM_IGNORED_PATTERNS, WOLFRAM_IGNORED_TITLES
from utils import BaseCog, FailureUI, optimize_image, save_image

if TYPE_CHECKING:
    from collections.abc import Sequence


LOGGER = logging.getLogger("WolframCog")


class PodType(StrEnum):
    """Standard Wolfram Pod IDs for logic routing."""

    INPUT = "Input"
    RESULT = "Result"
    SOLUTION = "Solution"
    PLOT = "Plot"


def format_math_text(text: str) -> str:
    """Format mathematical text for better readability."""
    # Replace 3.14159... with π
    text = re.sub(r"3\.14159\d+", "π", text)
    # Replace ' approx ' with ≈
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
            LOGGER.error("Wolfram network error: %s", e)
            raise WolframAPIError(f"Network error: {e}") from e
        except Exception as e:
            LOGGER.error("Wolfram query error: %s", e, exc_info=True)
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


class WolframCog(BaseCog):
    """Wolfram Alpha integration for solving equations and plotting."""

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(bot)

        self.client_session: aiohttp.ClientSession | None = None
        self.temp_dir = Path("temp")
        self._prepare_directories()

        self.ctx_menu = app_commands.ContextMenu(
            name="Solve with Wolfram",
            callback=self._context_solve,
        )
        self.bot.tree.add_command(self.ctx_menu)

    def _prepare_directories(self) -> None:
        self.temp_dir.mkdir(exist_ok=True, parents=True)

    @override
    async def cog_load(self) -> None:
        """Initialize persistent session."""
        self.client_session = aiohttp.ClientSession()
        LOGGER.info("WolframCog loaded.")

    @override
    async def cog_unload(self) -> None:
        """Cleanup session and commands."""
        if self.client_session:
            await self.client_session.close()
        self.bot.tree.remove_command(self.ctx_menu.name, type=self.ctx_menu.type)

    @app_commands.command(name="solve", description="Solve a mathematical equation")
    @app_commands.describe(problem="Equation to solve (e.g. x^2 + 2x + 1 = 0)")
    async def cmd_solve(self, interaction: Interaction, problem: str) -> None:
        """Slash command handler for solving."""
        await interaction.response.defer(ephemeral=True)
        LOGGER.info("Solve: %s | User: %s", problem, interaction.user)
        await self._handle_query(interaction, problem, mode="solve")

    @app_commands.command(name="plot", description="Plot a mathematical function")
    @app_commands.describe(function="Function to plot (e.g. sin(x)/x)")
    async def cmd_plot(self, interaction: Interaction, function: str) -> None:
        """Slash command handler for plotting."""
        await interaction.response.defer(ephemeral=True)
        LOGGER.info("Plot: %s | User: %s", function, interaction.user)
        await self._handle_query(interaction, function, mode="plot")

    async def _context_solve(
        self, interaction: Interaction, message: discord.Message
    ) -> None:
        """Context menu handler."""
        await interaction.response.defer(ephemeral=True)
        content = message.content.strip()

        if not content or len(content) > config.WOLFRAM_MAX_QUERY_LEN:
            await interaction.followup.send("Query empty or too long.", ephemeral=True)
            return

        LOGGER.info("Ctx Solve: %s | User: %s", content, interaction.user)
        await self._handle_query(interaction, content, mode="solve")

    # -------------------------------------------------------------------------
    # Logic Handlers
    # -------------------------------------------------------------------------

    async def _handle_query(
        self, interaction: Interaction, query: str, mode: Literal["solve", "plot"]
    ) -> None:
        """Unified handler for API interaction."""
        if not self.client_session:
            await FailureUI.send_failure(
                interaction,
                title="Internal Error",
                description="Session not initialized.",
            )
            return
        try:
            api = WolframClient(config.WOLFRAM_APP_ID, session=self.client_session)
        except ValueError:
            await FailureUI.send_failure(
                interaction, title="API Error", description="The API key is not set."
            )
            return

        final_query = f"{mode} {query}" if not query.lower().startswith(mode) else query

        try:
            result = await api.query(final_query)

            if not result.success:
                msg = result.error_msg or "Wolfram could not understand the input."
                await FailureUI.send_failure(
                    interaction, title="No Results", description=msg
                )
                return

            if mode == "plot" or (mode == "solve" and "plot" in query.lower()):
                if plot_url := result.plot_url:
                    return await self._send_plot(interaction, plot_url, query)
                elif mode == "plot":
                    await FailureUI.send_failure(
                        interaction, title="No Plot", description="No graph generated."
                    )
                    return
            await self._send_text_results(interaction, result, query)

        except WolframAPIError as e:
            await FailureUI.send_failure(
                interaction, title="API Error", description=str(e)
            )

    async def _send_text_results(
        self, interaction: Interaction, result: WolframResult, query: str
    ) -> None:
        """Construct and send the Embed."""
        input_pod = next((p for p in result.pods if p.id == "Input"), None)
        title_text = input_pod.get_joined_text() if input_pod else query
        title_text = title_text.replace("solve ", "").replace("plot ", "")

        embed = discord.Embed(
            title="Expression:", description=f"`{title_text}`", color=0xFFAE00
        )
        embed.set_author(name="StupidBot", icon_url=config.BOT_ICON)

        fields_added = 0
        for pod in result.pods:
            if pod.id == "Input":
                continue

            text = pod.get_joined_text()
            if not text:
                continue

            inline = not pod.is_primary
            text = shorten(text, width=1000)

            embed.add_field(
                name=f"{pod.title}:", value=f"```\n{text}\n```", inline=inline
            )
            fields_added += 1

            if fields_added >= 10:
                break

        if fields_added == 0:
            await interaction.followup.send(
                "No displayable results found.\n"
                "All results were filtered out.\n"
                f"Query: `{query}`",
                ephemeral=True,
            )
            return

        if not interaction.channel or not isinstance(
            interaction.channel, discord.abc.Messageable
        ):
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        msg = await interaction.channel.send(embed=embed)
        await interaction.followup.send(f"Results: {msg.jump_url}")

    async def _send_plot(self, interaction: Interaction, url: str, query: str) -> None:
        """Download, process, and send image."""
        try:
            path = save_image(
                image_url=url,
                save_to=self.temp_dir,
                resize=config.WOLFRAM_PLOT_RESIZE,
                quality=config.WOLFRAM_PLOT_QUALITY,
                format="WEBP",
            )

            optimize_image(
                input_path=path,
                max_size=config.WOLFRAM_PLOT_MAX_SIZE,
                quality=config.WOLFRAM_PLOT_QUALITY,
            )

            if not interaction.channel or not isinstance(
                interaction.channel, discord.abc.Messageable
            ):
                await interaction.followup.send(
                    "Cannot upload images in this context.", ephemeral=True
                )
                return

            file = File(path, filename=f"wolfram_plot_{path.name}")
            msg = await interaction.channel.send(
                content=f"{interaction.user.mention} **Plot:** `{query}`", file=file
            )
            await interaction.followup.send(f"Graph generated: {msg.jump_url}")

        except Exception as e:
            LOGGER.error("Image pipeline failed: %s", e, exc_info=True)
            await FailureUI.send_failure(
                interaction,
                title="Image Error",
                description="Failed to process graph image.",
            )

    @app_commands.command(
        name="wolfram_test", description="[Owner] Test batch Wolfram queries"
    )
    @app_commands.describe(category="Category to test (or 'all')")
    @app_commands.choices(
        category=[
            app_commands.Choice(name="All Categories", value="all"),
            app_commands.Choice(name="Algebra", value="Algebra"),
            app_commands.Choice(name="Calculus", value="Calculus"),
            app_commands.Choice(name="Trigonometry", value="Trigonometry"),
            app_commands.Choice(name="Complex Numbers", value="Complex Numbers"),
            app_commands.Choice(name="Linear Algebra", value="Linear Algebra"),
            app_commands.Choice(name="Number Theory", value="Number Theory"),
            app_commands.Choice(name="Plotting", value="Plotting"),
        ]
    )
    @app_commands.default_permissions(administrator=True)
    @commands.is_owner()
    async def cmd_test(self, interaction: Interaction, category: str = "all") -> None:
        """Owner-only command to batch test Wolfram queries.

        Tests multiple mathematical problems across different categories
        to verify API functionality and response formatting.

        Args:
            interaction: The Discord interaction.
            category: Category to test or 'all' for all categories.

        """
        # Owner check
        if interaction.user.id != self.bot.owner_id:
            await interaction.response.send_message(
                "❌ This command is restricted to the bot owner.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        if not self.client_session:
            await interaction.followup.send(
                "❌ Client session not initialized.",
                ephemeral=True,
            )
            return

        api = WolframClient(config.WOLFRAM_APP_ID, session=self.client_session)

        categories_to_test = TEST_QUERIES.keys() if category == "all" else [category]

        results: dict[str, dict[str, bool]] = {}
        total_tests = 0
        passed = 0
        failed = 0

        progress_msg = await interaction.followup.send(
            f"🔄 Testing {category}...\n`0/{sum(len(TEST_QUERIES.get(cat, [])) for cat in categories_to_test)}` queries processed",
            ephemeral=True,
            wait=True,
        )

        for cat in categories_to_test:
            queries = TEST_QUERIES.get(cat, [])
            results[cat] = {}

            for query in queries:
                total_tests += 1
                try:
                    mode = "plot" if cat == "Plotting" else "solve"
                    final_query = f"{mode} {query}"

                    result = await api.query(final_query)

                    if result.success and result.pods:
                        results[cat][query] = True
                        passed += 1
                    else:
                        results[cat][query] = False
                        failed += 1
                        LOGGER.warning(
                            "Test failed: %s | Error: %s", query, result.error_msg
                        )

                except Exception as e:
                    results[cat][query] = False
                    failed += 1
                    LOGGER.error("Test error: %s | Exception: %s", query, e)

                if total_tests % 3 == 0:
                    await progress_msg.edit(
                        content=f"🔄 Testing {category}...\n`{total_tests}/{sum(len(TEST_QUERIES.get(c, [])) for c in categories_to_test)}` queries processed"
                    )
                await asyncio.sleep(0.5)

        embed = discord.Embed(
            title="🧪 Wolfram Alpha Test Results",
            description=f"**Category:** {category.title()}\n**Total:** {total_tests} | **Passed:** ✅ {passed} | **Failed:** ❌ {failed}",
            color=0x00FF00 if failed == 0 else 0xFF6600,
        )

        for cat, queries in results.items():
            if not queries:
                continue

            status_lines: list[str] = []
            for query, success in queries.items():
                emoji = "✅" if success else "❌"
                display_query = query if len(query) <= 40 else query[:37] + "..."
                status_lines.append(f"{emoji} `{display_query}`")

            # Split into multiple fields if too long
            field_value = "\n".join(status_lines)
            if len(field_value) > 1024:
                mid = len(status_lines) // 2
                embed.add_field(
                    name=f"📊 {cat} (1/2)",
                    value="\n".join(status_lines[:mid]),
                    inline=False,
                )
                embed.add_field(
                    name=f"📊 {cat} (2/2)",
                    value="\n".join(status_lines[mid:]),
                    inline=False,
                )
            else:
                embed.add_field(
                    name=f"📊 {cat}",
                    value=field_value,
                    inline=False,
                )

        success_rate = (passed / total_tests * 100) if total_tests > 0 else 0
        embed.set_footer(text=f"Success Rate: {success_rate:.1f}%")

        await progress_msg.edit(content=None, embed=embed)

        # Log summary
        LOGGER.info(
            "Wolfram test completed: %d/%d passed (%.1f%%)",
            passed,
            total_tests,
            success_rate,
        )


TEST_QUERIES: Final[dict[str, list[str]]] = {
    "Algebra": [
        "x^2 + 2x + 1 = 0",
        "x^3 - 6x^2 + 11x - 6 = 0",
        "2x + 3y = 7, x - y = 1",
        "expand (x+2)^3",
        "factor x^4 - 16",
    ],
    "Calculus": [
        "derivative of sin(x)*cos(x)",
        "integrate x^2 from 0 to 5",
        "limit of (sin(x)/x) as x approaches 0",
        "second derivative of e^(x^2)",
        "integral of 1/(1+x^2)",
    ],
    "Trigonometry": [
        "sin(pi/4)",
        "tan(60 degrees)",
        "arcsin(0.5)",
        "solve sin(x) = 0.5",
    ],
    "Complex Numbers": [
        "(3+4i) * (2-i)",
        "sqrt(-1)",
        "e^(i*pi)",
    ],
    "Linear Algebra": [
        "eigenvalues {{1,2},{3,4}}",
        "determinant {{2,3},{1,4}}",
        "inverse {{1,2},{3,4}}",
    ],
    "Number Theory": [
        "prime factorization of 1234567",
        "gcd(48, 180)",
        "fibonacci(20)",
    ],
    "Plotting": [
        "sin(x)",
        "x^2 - 4",
        "e^(-x^2)",
        "tan(x) from -pi to pi",
        "x^3 - 3x^2 + 2",
        "1/x",
        "cos(x) + sin(2x)",
        "sqrt(x)",
    ],
}


async def setup(bot: commands.Bot) -> None:
    """Setup.

    Args:
        bot: BOT ITSELF

    """
    await bot.add_cog(WolframCog(bot))
