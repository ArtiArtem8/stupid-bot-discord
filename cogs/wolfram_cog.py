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

import logging
from pathlib import Path
from textwrap import shorten
from typing import Final, Literal, override

import aiohttp
import discord
from discord import File, Interaction, app_commands
from discord.ext import commands

import config
from api.wolfram import WolframAPIError, WolframClient, WolframResult
from framework import BaseCog, FeedbackType, FeedbackUI
from utils import optimize_image, save_image

LOGGER = logging.getLogger("WolframCog")


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
            await FeedbackUI.send(
                interaction,
                type=FeedbackType.WARNING,
                description="Query empty or too long.",
                ephemeral=True,
            )
            return

        LOGGER.info("Ctx Solve: %s | User: %s", content, interaction.user)
        await self._handle_query(interaction, content, mode="solve")

    async def _handle_query(
        self, interaction: Interaction, query: str, mode: Literal["solve", "plot"]
    ) -> None:
        """Unified handler for API interaction."""
        if not self.client_session:
            await FeedbackUI.send(
                interaction,
                type=FeedbackType.ERROR,
                title="Internal Error",
                description="Session not initialized.",
            )
            return
        try:
            api = WolframClient(config.WOLFRAM_APP_ID, session=self.client_session)
        except ValueError:
            await FeedbackUI.send(
                interaction,
                type=FeedbackType.ERROR,
                title="API Error",
                description="The API key is not set.",
            )
            return

        final_query = f"{mode} {query}" if not query.lower().startswith(mode) else query

        try:
            result = await api.query(final_query)

            if not result.success:
                msg = result.error_msg or "Wolfram could not understand the input."
                await FeedbackUI.send(
                    interaction,
                    type=FeedbackType.WARNING,
                    title="No Results",
                    description=msg,
                )
                return

            if mode == "plot" or (mode == "solve" and "plot" in query.lower()):
                if plot_url := result.plot_url:
                    return await self._send_plot(interaction, plot_url, query)
                elif mode == "plot":
                    await FeedbackUI.send(
                        interaction,
                        type=FeedbackType.WARNING,
                        title="No Plot",
                        description="No graph generated.",
                    )
                    return
            await self._send_text_results(interaction, result, query)

        except WolframAPIError as e:
            await FeedbackUI.send(
                interaction,
                type=FeedbackType.ERROR,
                title="API Error",
                description=str(e),
            )

    async def _send_text_results(
        self, interaction: Interaction, result: WolframResult, query: str
    ) -> None:
        """Construct and send the Embed."""
        input_pod = next((p for p in result.pods if p.id == "Input"), None)
        title_text = input_pod.get_joined_text() if input_pod else query
        title_text = title_text.replace("solve ", "").replace("plot ", "")

        embed = discord.Embed(
            title="Expression:", description=f"`{title_text}`", color=config.Color.INFO
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
            await FeedbackUI.send(
                interaction,
                type=FeedbackType.WARNING,
                description="No displayable results found.\n"
                "All results were filtered out.",
                title=f"Query: `{query}`",
                ephemeral=True,
            )
            return

        if not interaction.channel or not isinstance(
            interaction.channel, discord.abc.Messageable
        ):
            await FeedbackUI.send(interaction, embed=embed, ephemeral=True)
            return

        msg = await interaction.channel.send(embed=embed)
        await FeedbackUI.send(
            interaction,
            type=FeedbackType.SUCCESS,
            description=f"Results: {msg.jump_url}",
        )

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
                await FeedbackUI.send(
                    interaction,
                    type=FeedbackType.WARNING,
                    description="Cannot upload images in this context.",
                    ephemeral=True,
                )
                return

            file = File(path, filename=f"wolfram_plot_{path.name}")
            msg = await interaction.channel.send(
                content=f"{interaction.user.mention} **Plot:** `{query}`", file=file
            )
            await FeedbackUI.send(
                interaction,
                type=FeedbackType.SUCCESS,
                description=f"Graph generated: {msg.jump_url}",
            )

        except Exception as e:
            LOGGER.error("Image pipeline failed: %s", e, exc_info=True)
            await FeedbackUI.send(
                interaction,
                type=FeedbackType.ERROR,
                title="Image Error",
                description="Failed to process graph image.",
            )

    # @app_commands.command(
    #     name="wolfram_test", description="[Owner] Test batch Wolfram queries"
    # )
    # @app_commands.describe(category="Category to test (or 'all')")
    # @app_commands.choices(
    #     category=[
    #         app_commands.Choice(name="All Categories", value="all"),
    #         app_commands.Choice(name="Algebra", value="Algebra"),
    #         app_commands.Choice(name="Calculus", value="Calculus"),
    #         app_commands.Choice(name="Trigonometry", value="Trigonometry"),
    #         app_commands.Choice(name="Complex Numbers", value="Complex Numbers"),
    #         app_commands.Choice(name="Linear Algebra", value="Linear Algebra"),
    #         app_commands.Choice(name="Number Theory", value="Number Theory"),
    #         app_commands.Choice(name="Plotting", value="Plotting"),
    #     ]
    # )
    # @app_commands.default_permissions(administrator=True)
    # @commands.is_owner()
    # async def cmd_test(self, interaction: Interaction, category: str = "all") -> None:
    #     """Owner-only command to batch test Wolfram queries.

    #     Tests multiple mathematical problems across different categories
    #     to verify API functionality and response formatting.

    #     Args:
    #         interaction: The Discord interaction.
    #         category: Category to test or 'all' for all categories.

    #     """
    #     # Owner check
    #     if interaction.user.id != self.bot.owner_id:
    #         await FeedbackUI.send(
    #             interaction,
    #             type=FeedbackType.ERROR,
    #             description="❌ This command is restricted to the bot owner.",
    #             ephemeral=True,
    #         )
    #         return

    #     await interaction.response.defer(ephemeral=True)

    #     if not self.client_session:
    #         await FeedbackUI.send(
    #             interaction,
    #             type=FeedbackType.ERROR,
    #             description="❌ Client session not initialized.",
    #             ephemeral=True,
    #         )
    #         return

    #     api = WolframClient(config.WOLFRAM_APP_ID, session=self.client_session)

    #     categories_to_test = TEST_QUERIES.keys() if category == "all" else [category]

    #     results: dict[str, dict[str, bool]] = {}
    #     total_tests = 0
    #     passed = 0
    #     failed = 0

    #     progress_msg = await interaction.followup.send(
    #         f"🔄 Testing {category}...\n`0/{sum(len(TEST_QUERIES.get(cat, [])) for cat in categories_to_test)}` queries processed",
    #         ephemeral=True,
    #         wait=True,
    #     )

    #     for cat in categories_to_test:
    #         queries = TEST_QUERIES.get(cat, [])
    #         results[cat] = {}

    #         for query in queries:
    #             total_tests += 1
    #             try:
    #                 mode = "plot" if cat == "Plotting" else "solve"
    #                 final_query = f"{mode} {query}"

    #                 result = await api.query(final_query)

    #                 if result.success and result.pods:
    #                     results[cat][query] = True
    #                     passed += 1
    #                 else:
    #                     results[cat][query] = False
    #                     failed += 1
    #                     LOGGER.warning(
    #                         "Test failed: %s | Error: %s", query, result.error_msg
    #                     )

    #             except Exception as e:
    #                 results[cat][query] = False
    #                 failed += 1
    #                 LOGGER.error("Test error: %s | Exception: %s", query, e)

    #             if total_tests % 3 == 0:
    #                 await progress_msg.edit(
    #                     content=f"🔄 Testing {category}...\n`{total_tests}/{sum(len(TEST_QUERIES.get(c, [])) for c in categories_to_test)}` queries processed"
    #                 )
    #             await asyncio.sleep(0.5)

    #     embed = discord.Embed(
    #         title="🧪 Wolfram Alpha Test Results",
    #         description=f"**Category:** {category.title()}\n**Total:** {total_tests} | **Passed:** ✅ {passed} | **Failed:** ❌ {failed}",
    #         color=0x00FF00 if failed == 0 else 0xFF6600,
    #     )

    #     for cat, queries in results.items():
    #         if not queries:
    #             continue

    #         status_lines: list[str] = []
    #         for query, success in queries.items():
    #             emoji = "✅" if success else "❌"
    #             display_query = query if len(query) <= 40 else query[:37] + "..."
    #             status_lines.append(f"{emoji} `{display_query}`")

    #         # Split into multiple fields if too long
    #         field_value = "\n".join(status_lines)
    #         if len(field_value) > 1024:
    #             mid = len(status_lines) // 2
    #             embed.add_field(
    #                 name=f"📊 {cat} (1/2)",
    #                 value="\n".join(status_lines[:mid]),
    #                 inline=False,
    #             )
    #             embed.add_field(
    #                 name=f"📊 {cat} (2/2)",
    #                 value="\n".join(status_lines[mid:]),
    #                 inline=False,
    #             )
    #         else:
    #             embed.add_field(
    #                 name=f"📊 {cat}",
    #                 value=field_value,
    #                 inline=False,
    #             )

    #     success_rate = (passed / total_tests * 100) if total_tests > 0 else 0
    #     embed.set_footer(text=f"Success Rate: {success_rate:.1f}%")

    #     await progress_msg.edit(content=None, embed=embed)

    #     # Log summary
    #     LOGGER.info(
    #         "Wolfram test completed: %d/%d passed (%.1f%%)",
    #         passed,
    #         total_tests,
    #         success_rate,
    #     )


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
