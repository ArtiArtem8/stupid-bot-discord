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
from typing import Literal, override

import aiohttp
import discord
from discord import File, Interaction, app_commands
from discord.ext import commands

import config
from api.wolfram import WolframAPIError, WolframClient, WolframResult
from framework import BaseCog, FeedbackType, FeedbackUI
from utils import SafeEmbed, optimize_image, save_image

logger = logging.getLogger(__name__)


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
        logger.info("WolframCog loaded.")

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
        logger.info("Solve: %s | User: %s", problem, interaction.user)
        await self._handle_query(interaction, problem, mode="solve")

    @app_commands.command(name="plot", description="Plot a mathematical function")
    @app_commands.describe(function="Function to plot (e.g. sin(x)/x)")
    async def cmd_plot(self, interaction: Interaction, function: str) -> None:
        """Slash command handler for plotting."""
        await interaction.response.defer(ephemeral=True)
        logger.info("Plot: %s | User: %s", function, interaction.user)
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
                feedback_type=FeedbackType.WARNING,
                description="Query empty or too long.",
                ephemeral=True,
            )
            return

        logger.info("Ctx Solve: %s | User: %s", content, interaction.user)
        await self._handle_query(interaction, content, mode="solve")

    async def _handle_query(
        self, interaction: Interaction, query: str, mode: Literal["solve", "plot"]
    ) -> None:
        """Unified handler for API interaction."""
        if not self.client_session:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                title="Internal Error",
                description="Session not initialized.",
            )
            return
        try:
            api = WolframClient(config.WOLFRAM_APP_ID, session=self.client_session)
        except ValueError:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
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
                    feedback_type=FeedbackType.WARNING,
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
                        feedback_type=FeedbackType.WARNING,
                        title="No Plot",
                        description="No graph generated.",
                    )
                    return
            await self._send_text_results(interaction, result, query)

        except WolframAPIError as e:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
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

        embed = SafeEmbed(
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
            # SafeEmbed handles truncation and code block wrapping
            embed.add_code_field(name=f"{pod.title}:", value=text, inline=inline)
            fields_added += 1

            if fields_added >= 10:
                break

        if fields_added == 0:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description="No displayable results found.\n"
                + "All results were filtered out.",
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
            feedback_type=FeedbackType.SUCCESS,
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
                    feedback_type=FeedbackType.WARNING,
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
                feedback_type=FeedbackType.SUCCESS,
                description=f"Graph generated: {msg.jump_url}",
            )

        except Exception as e:
            logger.error("Image pipeline failed: %s", e, exc_info=True)
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                title="Image Error",
                description="Failed to process graph image.",
            )


async def setup(bot: commands.Bot) -> None:
    """Setup.

    Args:
        bot: BOT ITSELF

    """
    await bot.add_cog(WolframCog(bot))
