"""Block history and access management presentation."""

from collections.abc import Sequence
from enum import StrEnum

import discord
from discord.utils import format_dt

from stupidbot.common.text import TextPaginator, truncate_sequence, truncate_text
from stupidbot.discord_ui.embeds import SafeEmbed
from stupidbot.discord_ui.limits import MAX_EMBED_FIELD_LENGTH
from stupidbot.discord_ui.theme import Color
from stupidbot.features.access.models import BlockedUser, NameHistoryEntry


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
            color=Color.INFO,
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
        color=Color.INFO,
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


def format_recent_block_events(user: BlockedUser) -> str:
    """Format the five latest block events within one embed field."""
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
        max_length=MAX_EMBED_FIELD_LENGTH,
        separator="\n",
        placeholder="...",
    )


def format_name_history(history: Sequence[NameHistoryEntry]) -> str:
    """Format the three latest observed names within one embed field."""
    lines: list[str] = []
    for entry in sorted(history, key=lambda entry: entry.timestamp, reverse=True)[:3]:
        timestamp = format_dt(entry.timestamp, "D")
        name = truncate_text(entry.username, width=200)
        lines.append(f"{timestamp}:\n• Имя: {name}")
    return truncate_sequence(
        lines,
        max_length=MAX_EMBED_FIELD_LENGTH,
        separator="\n",
        placeholder="...",
    )


def format_blocked_user(
    user_entry: BlockedUser,
    member: discord.Member | None,
    *,
    show_details: bool,
) -> str:
    """Build one list entry using current Discord identity when available."""
    if member is None:
        user_info = f"Пользователь покинул сервер `{user_entry.user_id}`"
        current_username = user_entry.current_username
    else:
        user_info = f"{member.mention} `{member.id}`"
        current_username = member.display_name

    lines = [f"**Пользователь:** {user_info}"]
    if not show_details:
        return "\n".join(lines)

    username = truncate_text(current_username, width=80)
    lines.append(f"• Текущее имя: {username}")
    if not user_entry.block_history:
        lines.append("• История блокировок отсутствует.")
        return "\n".join(lines)

    last_block = user_entry.block_history[-1]
    reason = truncate_text(last_block.reason or "Не указана", width=200)
    timestamp = format_dt(last_block.timestamp, "R")
    lines.extend(
        [
            f"• Последняя блокировка: {timestamp}",
            f"• Причина: {reason}",
            f"• Администратор: <@{last_block.admin_id}>",
        ]
    )
    return "\n".join(lines)


BLOCK = BlockAction.BLOCK
UNBLOCK = BlockAction.UNBLOCK

ACTION_TITLES = {
    "block": "Блокировка",
    "unblock": "Разблокировка",
}
