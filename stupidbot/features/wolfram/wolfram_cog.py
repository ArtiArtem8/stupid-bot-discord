"""Own Wolfram commands, bounded concurrent requests and session shutdown."""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Literal, override

import aiohttp
import discord
from discord import File, Interaction, app_commands
from discord.ext import commands

from stupidbot import settings as config
from stupidbot.discord_ui.base_cog import BaseCog
from stupidbot.discord_ui.embeds import SafeEmbed
from stupidbot.discord_ui.feedback import FeedbackType, FeedbackUI
from stupidbot.discord_ui.theme import Color
from stupidbot.features.wolfram.client import (
    WolframAPIError,
    WolframClient,
    WolframRateLimitError,
    WolframResult,
)
from stupidbot.features.wolfram.messages import WolframMessages
from stupidbot.features.wolfram.plot_image import (
    ImageProcessingError,
    process_wolfram_plot,
)
from stupidbot.features.wolfram.presentation import (
    build_text_results_embed,
    wolfram_result_url,
)
from stupidbot.i18n.catalog import catalog, pgettext_noop
from stupidbot.i18n.discord import interaction_context

logger = logging.getLogger(__name__)
_INVALID_QUERY_MESSAGE = pgettext_noop("ui", "Query empty or too long.")
_WOLFRAM_FAILURE_MESSAGE = pgettext_noop(
    "ui", "Wolfram|Alpha request failed. Please try again later."
)
_WOLFRAM_RATE_LIMIT_MESSAGE = pgettext_noop(
    "ui", "Wolfram|Alpha is temporarily rate-limited. Please try again later."
)

type WolframMode = Literal["solve", "plot"]


def _normalize_query(query: str) -> str:
    """Normalize and validate one user-supplied Wolfram query."""
    normalized = query.strip()
    if not normalized or len(normalized) > config.WOLFRAM_MAX_QUERY_LEN:
        raise ValueError("Query empty or too long.")
    return normalized


def _wolfram_cooldown_key(interaction: Interaction) -> tuple[int | None, int]:
    """Share cooldowns per user within each guild or direct-message context."""
    return interaction.guild_id, interaction.user.id


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
        self._closing = False
        self._requests: set[asyncio.Task[object]] = set()

        self.ctx_menu = app_commands.ContextMenu(
            name=app_commands.locale_str("Solve with Wolfram", domain="wolfram"),
            callback=self._context_solve,
        )
        self.ctx_menu.add_check(self.interaction_check)
        self.bot.tree.add_command(self.ctx_menu)

    @override
    async def cog_load(self) -> None:
        """Initialize persistent session."""
        self.client_session = aiohttp.ClientSession()
        if config.WOLFRAM_APP_ID:
            self.wolfram_client = WolframClient(
                config.WOLFRAM_APP_ID, session=self.client_session
            )

    @override
    async def cog_unload(self) -> None:
        """Drain requests before closing the HTTP session and removing commands."""
        self._closing = True
        requests = tuple(self._requests)
        for task in requests:
            task.cancel()
        await asyncio.gather(*requests, return_exceptions=True)
        if self.client_session:
            await self.client_session.close()
        self.wolfram_client = None
        self.client_session = None
        self.bot.tree.remove_command(self.ctx_menu.name, type=self.ctx_menu.type)

    @app_commands.command(
        name="solve",
        description=app_commands.locale_str(
            "Solve a mathematical equation", domain="wolfram"
        ),
    )
    @app_commands.describe(
        problem=app_commands.locale_str(
            "Equation to solve (e.g. x^2 + 2x + 1 = 0)", domain="wolfram"
        )
    )
    @_wolfram_cooldown
    async def cmd_solve(
        self,
        interaction: Interaction,
        problem: app_commands.Range[str, 1, config.WOLFRAM_MAX_QUERY_LEN],
    ) -> None:
        await self._handle_query(interaction, problem, mode="solve")

    @app_commands.command(
        name="plot",
        description=app_commands.locale_str(
            "Plot a mathematical function", domain="wolfram"
        ),
    )
    @app_commands.describe(
        function=app_commands.locale_str(
            "Function to plot (e.g. sin(x)/x)", domain="wolfram"
        )
    )
    @_wolfram_cooldown
    async def cmd_plot(
        self,
        interaction: Interaction,
        function: app_commands.Range[str, 1, config.WOLFRAM_MAX_QUERY_LEN],
    ) -> None:
        await self._handle_query(interaction, function, mode="plot")

    @_wolfram_cooldown
    async def _context_solve(
        self, interaction: Interaction, message: discord.Message
    ) -> None:
        await self._handle_query(interaction, message.content, mode="solve")

    async def _handle_query(
        self, interaction: Interaction, query: str, mode: WolframMode
    ) -> None:
        """Own queued and active work until its response or cancellation completes."""
        if self._closing:
            await self._send_stopped(interaction)
            return
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Wolfram request requires an asyncio task")
        self._requests.add(task)
        try:
            await self._handle_admitted_query(interaction, query, mode)
        except asyncio.CancelledError:
            if self._closing:
                await self._send_stopped(interaction)
            raise
        finally:
            self._requests.discard(task)

    async def _send_stopped(self, interaction: Interaction) -> None:
        context = await interaction_context(interaction, private=True)
        messages = WolframMessages(catalog("wolfram", context.language))
        try:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.INFO,
                description=messages.catalog.pgettext(
                    "ui", "Wolfram request stopped during reload. Please try again."
                ),
                ephemeral=True,
            )
        except discord.HTTPException as exc:
            logger.warning("Cannot finish stopped Wolfram request: HTTP %s", exc.status)

    async def _handle_admitted_query(
        self, interaction: Interaction, query: str, mode: WolframMode
    ) -> None:
        context = await interaction_context(interaction, private=True)
        messages = WolframMessages(catalog("wolfram", context.language))
        try:
            query = _normalize_query(query)
        except ValueError:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description=messages.catalog.pgettext("ui", _INVALID_QUERY_MESSAGE),
                ephemeral=True,
            )
            return

        if not self.client_session:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                title=messages.catalog.pgettext("ui", "Internal Error"),
                description=messages.catalog.pgettext("ui", "Session not initialized."),
                ephemeral=True,
            )
            return
        if not self.wolfram_client:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                title=messages.catalog.pgettext("ui", "API-Key Error"),
                description=messages.catalog.pgettext("ui", "The API key is not set."),
                ephemeral=True,
            )
            return

        if config.MESSAGE_LOG_ENABLED:
            logger.info(
                "Wolfram %s: user_id=%s query=%r", mode, interaction.user.id, query
            )
        else:
            logger.info("Wolfram %s: user_id=%s", mode, interaction.user.id)
        async with self._request_semaphore:
            if self._closing:
                await self._send_stopped(interaction)
                return
            await self._execute_query(interaction, query, mode=mode)

    async def _execute_query(
        self, interaction: Interaction, query: str, *, mode: WolframMode
    ) -> None:
        """Execute one normalized query while the shared concurrency slot is held."""
        context = await interaction_context(interaction, private=True)
        messages = WolframMessages(catalog("wolfram", context.language))
        if not self.wolfram_client:
            await self._send_stopped(interaction)
            return
        wolfram_query = f"{mode} {query}"

        try:
            result = await self.wolfram_client.query(wolfram_query)
        except WolframRateLimitError:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                title=messages.catalog.pgettext("ui", "API Rate Limit Error"),
                description=messages.catalog.pgettext(
                    "ui", _WOLFRAM_RATE_LIMIT_MESSAGE
                ),
                ephemeral=True,
            )
            return
        except WolframAPIError:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                title=messages.catalog.pgettext("ui", "API Error"),
                description=messages.catalog.pgettext("ui", _WOLFRAM_FAILURE_MESSAGE),
                ephemeral=True,
            )
            return

        await self._send_query_result(
            interaction,
            result,
            query=query,
            wolfram_query=wolfram_query,
            mode=mode,
        )

    async def _send_query_result(
        self,
        interaction: Interaction,
        result: WolframResult,
        *,
        query: str,
        wolfram_query: str,
        mode: WolframMode,
    ) -> None:
        """Send parsed Wolfram results through Discord feedback."""
        context = await interaction_context(interaction, private=True)
        messages = WolframMessages(catalog("wolfram", context.language))
        if not result.success:
            if result.failure_reason is not None:
                description = messages.failure(result.failure_reason)
            else:
                description = result.error_msg or messages.catalog.pgettext(
                    "ui", "Wolfram could not understand the input."
                )
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                title=messages.catalog.pgettext("ui", "No Results"),
                description=description,
                ephemeral=True,
            )
            return

        if mode == "plot" and (plot_url := result.plot_url):
            await self._send_plot(
                interaction,
                plot_url,
                query,
                result_query=wolfram_query,
            )
            return
        if mode == "plot":
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                title=messages.catalog.pgettext("ui", "No Plot"),
                description=messages.catalog.pgettext("ui", "No graph generated."),
                ephemeral=True,
            )
            return
        await self._send_text_results(interaction, result, query)

    async def _send_text_results(
        self, interaction: Interaction, result: WolframResult, query: str
    ) -> None:
        """Publish displayable text pods and acknowledge the interaction."""
        context = await interaction_context(interaction, private=True)
        messages = WolframMessages(catalog("wolfram", context.language))
        embed = build_text_results_embed(result, query, language=context.language)
        if not embed.fields:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description=messages.catalog.pgettext(
                    "ui",
                    "No displayable results found.\nAll results were filtered out.",
                ),
                title=messages.query(query=query),
                ephemeral=True,
            )
            return

        if not interaction.channel or not isinstance(
            interaction.channel, discord.abc.Messageable
        ):
            await FeedbackUI.send(interaction, embed=embed, ephemeral=True)
            return

        public = await interaction_context(interaction, private=False)
        embed = build_text_results_embed(result, query, language=public.language)
        msg = await interaction.channel.send(embed=embed)
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.SUCCESS,
            description=messages.results(url=msg.jump_url),
            ephemeral=True,
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
        context = await interaction_context(interaction, private=True)
        messages = WolframMessages(catalog("wolfram", context.language))
        channel = interaction.channel
        if not channel or not isinstance(channel, discord.abc.Messageable):
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description=messages.catalog.pgettext(
                    "ui", "Cannot upload images in this context."
                ),
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
                description=messages.graph(url=msg.jump_url),
                ephemeral=True,
            )
        except WolframRateLimitError:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                title=messages.catalog.pgettext("ui", "API Rate Limit Error"),
                description=messages.catalog.pgettext(
                    "ui", _WOLFRAM_RATE_LIMIT_MESSAGE
                ),
                ephemeral=True,
            )
        except (WolframAPIError, ImageProcessingError):
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
        context = await interaction_context(interaction, private=False)
        messages = WolframMessages(catalog("wolfram", context.language))
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
            max_size=config.WOLFRAM_PLOT_MAX_SIZE,
            max_source_pixels=config.WOLFRAM_PLOT_MAX_SOURCE_PIXELS,
            max_output_bytes=upload_budget,
        )

        with io.BytesIO(output) as buffer:
            file = File(buffer, filename="wolfram_plot.webp")
            try:
                result_url = wolfram_result_url(result_query)
                caption = messages.plot(
                    mention=interaction.user.mention, url=result_url, query=query
                )
                if len(caption) <= 2_000:
                    return await channel.send(
                        content=caption, file=file, suppress_embeds=True
                    )
                # Encoding a Unicode query can make the URL too long for a message.
                # The full caption still fits in an embed.
                embed = SafeEmbed(
                    description=messages.plot(
                        mention="", url=result_url, query=query
                    ).strip(),
                    color=Color.INFO,
                )
                embed.set_image(url="attachment://wolfram_plot.webp")
                return await channel.send(
                    content=interaction.user.mention, embed=embed, file=file
                )
            finally:
                file.close()

    async def _send_plot_error(self, interaction: Interaction) -> None:
        """Send the stable user-facing response for image pipeline failures."""
        context = await interaction_context(interaction, private=True)
        messages = WolframMessages(catalog("wolfram", context.language))
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.ERROR,
            title=messages.catalog.pgettext("ui", "Image Error"),
            description=messages.catalog.pgettext(
                "ui", "Failed to process graph image."
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    """Register the Wolfram cog."""
    await bot.add_cog(WolframCog(bot))
