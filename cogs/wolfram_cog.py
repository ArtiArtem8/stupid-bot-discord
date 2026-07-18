"""Wolfram Alpha integration for mathematical problem solving.

Features:
- Asynchronous HTTP calls via aiohttp
- Strict XML parsing for stability
- Specialized mathematical output formatting
- Image optimization pipeline
- Type-safe architecture (Python 3.12+)

Requirements:
    WOLFRAM_APP_ID environment variable must be set
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Literal, override
from urllib.parse import quote_plus

import aiohttp
import discord
from discord import File, Interaction, app_commands
from discord.ext import commands

import config
from api.wolfram import (
    WolframAPIError,
    WolframClient,
    WolframRateLimitError,
    WolframResult,
)
from framework import BaseCog, FeedbackType, FeedbackUI
from utils import (
    CharacterLimitExceededError,
    ImageProcessingError,
    SafeEmbed,
    process_wolfram_plot,
)

logger = logging.getLogger(__name__)
_WOLFRAM_RESULT_URL = "https://www.wolframalpha.com/input?i="
_INVALID_QUERY_MESSAGE = "Query empty or too long."
_WOLFRAM_FAILURE_MESSAGE = "Wolfram|Alpha request failed. Please try again later."
_WOLFRAM_RATE_LIMIT_MESSAGE = (
    "Wolfram|Alpha is temporarily rate-limited. Please try again later."
)
_MAX_TEXT_RESULT_FIELDS = 10


def _wolfram_result_url(query: str) -> str:
    """Build the public Wolfram result URL used for attribution."""
    return f"{_WOLFRAM_RESULT_URL}{quote_plus(query)}"


def _normalize_query(query: str) -> str:
    """Normalize and validate one user-supplied Wolfram query."""
    normalized = query.strip()
    if not normalized or len(normalized) > config.WOLFRAM_MAX_QUERY_LEN:
        raise ValueError("Query empty or too long.")
    return normalized


def _wolfram_cooldown_key(interaction: Interaction) -> tuple[int | None, int]:
    """Share cooldowns per user within each guild or direct-message context."""
    return interaction.guild_id, interaction.user.id


def _build_text_results_embed(result: WolframResult, query: str) -> SafeEmbed:
    """Build an embed from the displayable text pods that fit Discord's limits."""
    input_pod = next((pod for pod in result.pods if pod.id == "Input"), None)
    title_text = input_pod.get_joined_text() if input_pod else query
    title_text = title_text.replace("solve ", "").replace("plot ", "")

    embed = SafeEmbed(
        title="Expression:", description=f"`{title_text}`", color=config.Color.INFO
    )
    embed.set_author(name="StupidBot", icon_url=config.BOT_ICON)

    for pod in result.pods:
        if pod.id == "Input" or not (text := pod.get_joined_text()):
            continue
        try:
            embed.add_code_field(
                name=f"{pod.title}:", value=text, inline=not pod.is_primary
            )
        except CharacterLimitExceededError:
            break
        if len(embed.fields) >= _MAX_TEXT_RESULT_FIELDS:
            break

    return embed


_wolfram_cooldown = app_commands.checks.cooldown(
    1,
    5.0,
    key=_wolfram_cooldown_key,
)


class WolframCog(BaseCog):
    """Wolfram Alpha integration for solving equations and plotting."""

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(bot)

        self.client_session: aiohttp.ClientSession | None = None
        self.wolfram_client: WolframClient | None = None
        self._request_semaphore = asyncio.Semaphore(2)

        self.ctx_menu = app_commands.ContextMenu(
            name="Solve with Wolfram",
            callback=self._context_solve,
        )
        self.bot.tree.add_command(self.ctx_menu)

    @override
    async def cog_load(self) -> None:
        """Initialize persistent session."""
        self.client_session = aiohttp.ClientSession()
        if config.WOLFRAM_APP_ID:
            self.wolfram_client = WolframClient(
                config.WOLFRAM_APP_ID, session=self.client_session
            )
        logger.info("WolframCog loaded.")

    @override
    async def cog_unload(self) -> None:
        """Cleanup session and commands."""
        if self.client_session:
            await self.client_session.close()
        self.wolfram_client = None
        self.client_session = None
        self.bot.tree.remove_command(self.ctx_menu.name, type=self.ctx_menu.type)

    @app_commands.command(name="solve", description="Solve a mathematical equation")
    @app_commands.describe(problem="Equation to solve (e.g. x^2 + 2x + 1 = 0)")
    @_wolfram_cooldown
    async def cmd_solve(
        self,
        interaction: Interaction,
        problem: app_commands.Range[str, 1, config.WOLFRAM_MAX_QUERY_LEN],
    ) -> None:
        """Slash command handler for solving."""
        await interaction.response.defer(ephemeral=True)
        await self._handle_query(interaction, problem, mode="solve")

    @app_commands.command(name="plot", description="Plot a mathematical function")
    @app_commands.describe(function="Function to plot (e.g. sin(x)/x)")
    @_wolfram_cooldown
    async def cmd_plot(
        self,
        interaction: Interaction,
        function: app_commands.Range[str, 1, config.WOLFRAM_MAX_QUERY_LEN],
    ) -> None:
        """Slash command handler for plotting."""
        await interaction.response.defer(ephemeral=True)
        await self._handle_query(interaction, function, mode="plot")

    @_wolfram_cooldown
    async def _context_solve(
        self, interaction: Interaction, message: discord.Message
    ) -> None:
        """Context menu handler."""
        await interaction.response.defer(ephemeral=True)
        await self._handle_query(interaction, message.content, mode="solve")

    async def _handle_query(
        self, interaction: Interaction, query: str, mode: Literal["solve", "plot"]
    ) -> None:
        """Unified handler for API interaction."""
        try:
            query = _normalize_query(query)
        except ValueError:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description=_INVALID_QUERY_MESSAGE,
                ephemeral=True,
            )
            return

        if not self.client_session:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                title="Internal Error",
                description="Session not initialized.",
            )
            return
        if not self.wolfram_client:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                title="API-Key Error",
                description="The API key is not set.",
            )
            return

        logger.info("Wolfram %s: %s | User: %s", mode, query, interaction.user)
        async with self._request_semaphore:
            await self._execute_query(interaction, query, mode=mode)

    async def _execute_query(
        self, interaction: Interaction, query: str, *, mode: Literal["solve", "plot"]
    ) -> None:
        """Execute one normalized query while the shared concurrency slot is held."""
        if not self.wolfram_client:
            return
        final_query = f"{mode} {query}" if not query.lower().startswith(mode) else query

        try:
            result = await self.wolfram_client.query(final_query)
        except WolframRateLimitError:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                title="API Rate Limit Error",
                description=_WOLFRAM_RATE_LIMIT_MESSAGE,
            )
            return
        except WolframAPIError:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                title="API Error",
                description=_WOLFRAM_FAILURE_MESSAGE,
            )
            return

        await self._send_query_result(
            interaction,
            result,
            query=query,
            final_query=final_query,
            mode=mode,
        )

    async def _send_query_result(
        self,
        interaction: Interaction,
        result: WolframResult,
        *,
        query: str,
        final_query: str,
        mode: Literal["solve", "plot"],
    ) -> None:
        """Dispatch a parsed Wolfram result to the existing Discord feedback path."""
        if not result.success:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                title="No Results",
                description=(
                    result.error_msg or "Wolfram could not understand the input."
                ),
            )
            return

        plot_expected = mode == "plot" or (mode == "solve" and "plot" in query.lower())
        if plot_expected and (plot_url := result.plot_url):
            await self._send_plot(
                interaction,
                plot_url,
                query,
                result_query=final_query,
            )
            return
        if mode == "plot":
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                title="No Plot",
                description="No graph generated.",
            )
            return
        await self._send_text_results(interaction, result, query)

    async def _send_text_results(
        self, interaction: Interaction, result: WolframResult, query: str
    ) -> None:
        """Construct and send the Embed."""
        embed = _build_text_results_embed(result, query)
        if not embed.fields:
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

    async def _send_plot(
        self,
        interaction: Interaction,
        url: str,
        query: str,
        *,
        result_query: str | None = None,
    ) -> None:
        """Download, process, and send image."""
        channel = interaction.channel
        if not channel or not isinstance(channel, discord.abc.Messageable):
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description="Cannot upload images in this context.",
                ephemeral=True,
            )
            return

        try:
            msg = await self._upload_plot(
                channel,
                interaction,
                url,
                query,
                result_query=result_query or query,
            )
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.SUCCESS,
                description=f"Graph generated: {msg.jump_url}",
            )
        except WolframRateLimitError:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                title="API Rate Limit Error",
                description=_WOLFRAM_RATE_LIMIT_MESSAGE,
            )
        except (WolframAPIError, ImageProcessingError):
            await self._send_plot_error(interaction)
        except Exception:
            logger.exception("Unexpected Wolfram image pipeline failure")
            await self._send_plot_error(interaction)

    async def _upload_plot(
        self,
        channel: discord.abc.Messageable,
        interaction: Interaction,
        url: str,
        query: str,
        *,
        result_query: str,
    ) -> discord.Message:
        """Download, process, and upload one plot while resources are open."""
        if not self.wolfram_client:
            raise WolframAPIError("Wolfram client is not initialized")

        source = await self.wolfram_client.fetch_plot_image(
            url, max_bytes=config.WOLFRAM_PLOT_MAX_DOWNLOAD_BYTES
        )
        upload_budget = min(
            config.WOLFRAM_PLOT_MAX_UPLOAD_BYTES,
            interaction.filesize_limit,
        )
        output = await asyncio.to_thread(
            process_wolfram_plot,
            source,
            target_width=config.WOLFRAM_PLOT_TARGET_WIDTH,
            max_size=config.WOLFRAM_PLOT_MAX_SIZE,
            max_source_pixels=config.WOLFRAM_PLOT_MAX_SOURCE_PIXELS,
            max_output_bytes=upload_budget,
            quality=config.WOLFRAM_PLOT_QUALITY,
            fallback_qualities=config.WOLFRAM_PLOT_FALLBACK_QUALITIES,
        )

        with io.BytesIO(output) as buffer:
            file = File(buffer, filename="wolfram_plot.webp")
            try:
                result_url = _wolfram_result_url(result_query)
                return await channel.send(
                    content=(
                        f"{interaction.user.mention} **Plot:** `{query}`\n"
                        f"**[View on Wolfram|Alpha]({result_url})**"
                    ),
                    file=file,
                )
            finally:
                file.close()

    async def _send_plot_error(self, interaction: Interaction) -> None:
        """Preserve the command's existing image failure feedback."""
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
