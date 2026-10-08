"""Administrative commands for user blocking and management.

Provides:
- Blocking/unblocking users from bot access
- Viewing detailed block history
- Listing all blocked users
- Tracking name changes over time

"""

import logging
from collections.abc import Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, NoReturn, override

import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import format_dt

import config
from api.blocking import BlockManager
from api.blocking_models import BlockedUser, NameHistoryEntry
from framework.base_cog import BaseCog
from framework.checks import is_owner_app
from framework.feedback_ui import FeedbackType, FeedbackUI
from framework.pagination import BasePaginator
from resources import ACTION_TITLES
from utils.embeds import SafeEmbed
from utils.text_utils import TextPaginator, truncate_sequence, truncate_text

if TYPE_CHECKING:
    from framework.bot import StupidBot

logger = logging.getLogger(__name__)


class BlockedListPages:
    """Keep the full blocked-user snapshot in bounded, individually shown embeds."""

    def __init__(self, entries: list[str], *, show_details: bool) -> None:
        self.pages = TextPaginator(entries, page_size=15, max_length=3800).pages
        self.total = len(entries)
        self.show_details = show_details

    async def get_page_count(self) -> int:
        return len(self.pages)

    def make_embed(self, page: int) -> discord.Embed:
        embed = SafeEmbed(
            title=f"Заблокированные пользователи ({self.total})",
            description=self.pages[page],
            color=config.Color.INFO,
        )
        detail = " • Детальная информация о блокировках" if self.show_details else ""
        embed.set_footer(text=f"Страница {page + 1}/{len(self.pages)}{detail}")
        return embed

    async def on_unauthorized(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Эти страницы доступны только автору команды.", ephemeral=True
        )


class BlockAction(StrEnum):
    """Action types for block/unblock commands."""

    BLOCK = "block"
    UNBLOCK = "unblock"


BLOCK = BlockAction.BLOCK
UNBLOCK = BlockAction.UNBLOCK


def create_block_embed(
    user: discord.Member,
    action: BlockAction,
    reason: str | None = None,
) -> discord.Embed:
    """Create standardized embed for block/unblock actions.

    Args:
        user: User being blocked/unblocked
        action: "block" or "unblock"
        reason: Optional reason for action

    Returns:
        Formatted Discord embed

    """
    description = f"{user.mention} был {'за' if action == BLOCK else 'раз'}блокирован"
    title = ACTION_TITLES[action]
    embed = SafeEmbed(
        title=title,
        description=description,
        color=config.Color.INFO,
    )

    if reason:
        embed.safe_add_field(
            name="Причина",
            value=reason,
            inline=False,
        )

    return embed


def format_danger_level(block_count: int) -> str:
    """Determine danger level emoji based on block count.

    Args:
        block_count: Number of times user was blocked

    Returns:
        Emoji string representing danger level

    """
    if block_count <= 2:
        return "🟢 Низкий"
    if block_count <= 4:
        return "🟠 Средний"
    return "🔴 Высокий"


def _format_recent_block_events(user: BlockedUser) -> str:
    events = sorted(
        [(entry.timestamp, "BLOCK", entry) for entry in user.block_history]
        + [(entry.timestamp, "UNBLOCK", entry) for entry in user.unblock_history],
        key=lambda event: event[0],
        reverse=True,
    )[:5]
    lines: list[str] = []
    for timestamp, action, entry in events:
        icon = "🔒" if action == "BLOCK" else "🔓"
        reason = truncate_text(entry.reason or "Не указана", width=200, mode="middle")
        lines.append(
            f"{icon} **{action}** {format_dt(timestamp, 'R')}\n"
            f"• Админ: <@{entry.admin_id}>\n"
            f"• Причина: {reason}"
        )
    return truncate_sequence(
        lines,
        max_length=config.MAX_EMBED_FIELD_LENGTH,
        separator="\n",
        placeholder="...",
    )


def _format_name_history(history: Sequence[NameHistoryEntry]) -> str:
    lines: list[str] = []
    for entry in sorted(history, key=lambda entry: entry.timestamp, reverse=True)[:3]:
        timestamp = format_dt(entry.timestamp, "D")
        name = truncate_text(entry.username, width=200)
        lines.append(f"{timestamp}:\n• Имя: {name}")
    return truncate_sequence(
        lines,
        max_length=config.MAX_EMBED_FIELD_LENGTH,
        separator="\n",
        placeholder="...",
    )


class AdminCog(BaseCog):
    """Administrative commands for server management.

    Default command permissions target administrators; owner checks stay explicit.
    """

    def __init__(self, bot: commands.Bot, block_manager: BlockManager) -> None:
        super().__init__(bot)
        self.block_manager = block_manager

    @override
    def should_bypass_block(self, interaction: discord.Interaction) -> bool:
        """Allow admin commands to bypass block checks."""
        return True

    @app_commands.command(name="error-test", description="Тестирование ошибок")
    @is_owner_app()
    @app_commands.default_permissions(administrator=True)
    async def error(self, _: discord.Interaction) -> NoReturn:
        raise RuntimeError("Test error")

    @app_commands.command(
        name="block", description="Заблокировать пользователя от использования бота."
    )
    @app_commands.describe(
        user="Пользователь, которого надо лишить доступа к этому боту",
        reason="Причина блокировки",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def block(
        self, interaction: discord.Interaction, user: discord.Member, reason: str = ""
    ) -> None:
        guild = await self._require_guild(interaction)
        if await self.block_manager.is_user_blocked(guild.id, user.id):
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description=f"{user.mention} уже заблокирован.",
                ephemeral=True,
            )
            return
        await self.block_manager.block_user(guild.id, user, interaction.user.id, reason)
        logger.info("Blocked user %d in guild %d", user.id, guild.id)
        embed = create_block_embed(user, BLOCK, reason)
        await FeedbackUI.send(interaction, embed=embed, ephemeral=True)

    @app_commands.command(
        name="unblock",
        description="Снять блокировку использования бота с пользователя.",
    )
    @app_commands.describe(
        user="Пользователь, с которого снимается блокировка",
        reason="Причина снятия блокировки",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def unblock(
        self, interaction: discord.Interaction, user: discord.Member, reason: str = ""
    ) -> None:
        guild = await self._require_guild(interaction)
        if not await self.block_manager.is_user_blocked(guild.id, user.id):
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description=f"{user.mention} не заблокирован.",
                ephemeral=True,
            )
            return
        await self.block_manager.unblock_user(
            guild.id, user, interaction.user.id, reason
        )
        logger.info("Unblocked user %d in guild %d", user.id, guild.id)
        embed = create_block_embed(user, UNBLOCK, reason)
        await FeedbackUI.send(interaction, embed=embed, ephemeral=True)

    @app_commands.command(
        name="block-info",
        description="Показать подробную информацию о блокировках пользователя.",
    )
    @app_commands.describe(
        user="Пользователь для просмотра информации",
        ephemeral="Скрыть сообщение от других пользователей",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def blockinfo(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        ephemeral: bool = True,
    ) -> None:
        guild = await self._require_guild(interaction)
        user_entry = await self.block_manager.get_user(guild.id, user.id)

        if not user_entry or not user_entry.block_history:
            logger.info(
                "No block history found for user %s in guild %s (%s)",
                user.id,
                guild.name,
                guild.id,
            )
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.INFO,
                description=f"{user.mention} не имеет истории блокировок.",
                ephemeral=ephemeral,
            )
            return

        logger.info(
            "Displaying block history for user %s in guild %s (%s)",
            user.id,
            guild.name,
            guild.id,
        )
        embed = SafeEmbed(
            title="Полная история блокировок",
            color=config.Color.ERROR if user_entry.is_blocked else config.Color.SUCCESS,
        )
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)
        embed.set_thumbnail(url=user.display_avatar.url)

        if user_entry.is_blocked:
            last_block = user_entry.block_history[-1]
            timestamp = format_dt(last_block.timestamp, "F")
            status_value = (
                f"**Заблокирован**\n"
                f"• Администратор: <@{last_block.admin_id}>\n"
                f"• Дата: {timestamp}\n"
                f"• Причина: {last_block.reason or 'Не указана'}\n"
            )
        else:
            status_value = "Не заблокирован"

        embed.safe_add_field(
            name="Текущий статус",
            value=status_value,
            inline=False,
        )

        embed.safe_add_field(
            name="Последние события",
            value=_format_recent_block_events(user_entry),
            inline=False,
        )
        if user_entry.name_history:
            embed.safe_add_field(
                name="История имён",
                value=_format_name_history(user_entry.name_history),
            )

        first_block_ts = format_dt(user_entry.block_history[0].timestamp, "D")
        stats = [
            f"• Всего блокировок: {len(user_entry.block_history)}",
            f"• Всего разблокировок: {len(user_entry.unblock_history)}",
            f"• Первая блокировка: {first_block_ts}",
        ]

        if user_entry.unblock_history:
            last_unblock_ts = format_dt(user_entry.unblock_history[-1].timestamp, "D")
            stats.append(f"• Последняя разблокировка: {last_unblock_ts}")

        embed.safe_add_field(
            name="Статистика",
            value="\n".join(stats),
            inline=False,
        )

        danger_level = format_danger_level(len(user_entry.block_history))
        embed.set_footer(text=f"Уровень проблемности: {danger_level}")

        await FeedbackUI.send(interaction, embed=embed, ephemeral=ephemeral)

        logger.info("Displayed blockinfo for user %s in guild %s", user.id, guild.id)

    @app_commands.command(
        name="list-blocked", description="Показать всех заблокированных пользователей"
    )
    @app_commands.describe(
        show_details="Показать дополнительную информацию",
        ephemeral="Скрыть сообщение от других пользователей",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def listblocked(
        self,
        interaction: discord.Interaction,
        show_details: bool = False,
        ephemeral: bool = True,
    ) -> None:
        guild = await self._require_guild(interaction)
        all_users = await self.block_manager.get_guild_users(guild.id)
        blocked_users = [u for u in all_users if u.is_blocked]

        if not blocked_users:
            logger.info("No blocked users found in guild %s", guild.id)
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.INFO,
                description="Нет заблокированных пользователей.",
                ephemeral=ephemeral,
            )
            return
        logger.info("Found %s blocked users in guild %s", len(blocked_users), guild.id)

        entries: list[str] = []

        for user_entry in blocked_users:
            user = guild.get_member(user_entry.user_id)
            if user is None:
                user_info = f"Пользователь покинул сервер `{user_entry.user_id}`"
                current_username = user_entry.current_username
            else:
                user_info = f"{user.mention} `{user.id}`"
                current_username = user.display_name

            entry = [f"**Пользователь:** {user_info}"]

            if show_details:
                last_block = user_entry.block_history[-1]
                truncated_username = truncate_text(current_username, width=80)
                truncated_reason = truncate_text(
                    last_block.reason or "Не указана", width=200
                )
                ts = format_dt(last_block.timestamp, "R")
                entry.extend(
                    [
                        f"• Текущее имя: {truncated_username}",
                        f"• Последняя блокировка: {ts}",
                        f"• Причина: {truncated_reason}",
                        f"• Администратор: <@{last_block.admin_id}>",
                    ]
                )

            entries.append("\n".join(entry))
        pages = BlockedListPages(entries, show_details=show_details)
        view = BasePaginator(pages, interaction.user.id)
        await view.prepare()
        await view.send(interaction, embed=view.make_embed(), ephemeral=ephemeral)

    @app_commands.command(
        name="del", description="Удалить сообщение по ID (только владелец)."
    )
    @is_owner_app()
    @app_commands.describe(message_id="ID сообщения для удаления")
    @app_commands.default_permissions(administrator=True)
    async def delete_message(
        self, interaction: discord.Interaction, message_id: str
    ) -> None:
        channel = interaction.channel
        if channel is None or not isinstance(channel, discord.abc.Messageable):
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description="Невозможно удалить сообщение в этом канале.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            msg = await channel.fetch_message(int(message_id))
            await msg.delete()
        except (discord.NotFound, ValueError):
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description="Сообщение не найдено.",
                ephemeral=True,
            )
            return
        except discord.Forbidden:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description="Нет прав.",
                ephemeral=True,
            )
            return

        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.SUCCESS,
            description="Удалено.",
            ephemeral=True,
            delete_after=1.0,
        )


async def setup(bot: "StupidBot") -> None:
    """Register the administration cog."""
    await bot.add_cog(AdminCog(bot, bot.block_manager))
