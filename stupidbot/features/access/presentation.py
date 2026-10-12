"""Block history and access management presentation."""

from collections.abc import Sequence
from enum import StrEnum

import discord
from discord.utils import format_dt

from stupidbot.common.text import TextPaginator, truncate_sequence, truncate_text
from stupidbot.discord_ui.embeds import SafeEmbed
from stupidbot.discord_ui.limits import MAX_EMBED_FIELD_LENGTH
from stupidbot.discord_ui.theme import Color
from stupidbot.features.access.messages import AccessMessages
from stupidbot.features.access.models import BlockedUser, NameHistoryEntry
from stupidbot.i18n.catalog import catalog, pgettext_noop
from stupidbot.i18n.discord import interaction_context
from stupidbot.i18n.policy import Language


class BlockedListPages:
    """Keep the full blocked-user snapshot in bounded, individually shown embeds."""

    def __init__(
        self,
        entries: list[str],
        *,
        show_details: bool,
        language: Language = Language.RU,
    ) -> None:
        self.messages = AccessMessages(catalog("access", language))
        self.pages = TextPaginator(entries, page_size=15, max_length=3800).pages
        self.total = len(entries)
        self.show_details = show_details

    async def get_page_count(self) -> int:
        return len(self.pages)

    def make_embed(self, page: int) -> discord.Embed:
        embed = SafeEmbed(
            title=self.messages.list_title(count=self.total),
            description=self.pages[page],
            color=Color.INFO,
        )
        detail = (
            self.messages.catalog.pgettext(
                "ui", " • Детальная информация о блокировках"
            )
            if self.show_details
            else ""
        )
        embed.set_footer(
            text=self.messages.list_footer(
                page=page + 1, pages=len(self.pages), detail=detail
            )
        )
        return embed

    async def on_unauthorized(self, interaction: discord.Interaction) -> None:
        context = await interaction_context(interaction, private=True)
        messages = AccessMessages(catalog("access", context.language))
        await interaction.response.send_message(
            messages.catalog.pgettext(
                "ui", "Эти страницы доступны только автору команды."
            ),
            ephemeral=True,
        )


class BlockAction(StrEnum):
    """Action types for block/unblock commands."""

    BLOCK = "block"
    UNBLOCK = "unblock"


def create_block_embed(
    user: discord.Member,
    action: BlockAction,
    reason: str | None = None,
    *,
    language: Language = Language.RU,
) -> discord.Embed:
    """Create standardized embed for block/unblock actions.

    Args:
        user: User being blocked/unblocked
        action: "block" or "unblock"
        reason: Optional reason for action
        language: Language for the action labels.

    Returns:
        Formatted Discord embed

    """
    messages = AccessMessages(catalog("access", language))
    description = (
        messages.blocked(mention=user.mention)
        if action == BLOCK
        else messages.unblocked(mention=user.mention)
    )
    title = messages.catalog.pgettext("ui", ACTION_TITLES[action])
    embed = SafeEmbed(
        title=title,
        description=description,
        color=Color.INFO,
    )

    if reason:
        embed.safe_add_field(
            name=messages.catalog.pgettext("ui", "Причина"),
            value=reason,
            inline=False,
        )

    return embed


def format_danger_level(block_count: int, *, language: Language = Language.RU) -> str:
    """Determine danger level emoji based on block count.

    Args:
        block_count: Number of times user was blocked
        language: Language for the severity label.

    Returns:
        Emoji string representing danger level

    """
    messages = AccessMessages(catalog("access", language))
    if block_count <= 2:
        return messages.catalog.pgettext("ui", "🟢 Низкий")
    if block_count <= 4:
        return messages.catalog.pgettext("ui", "🟠 Средний")
    return messages.catalog.pgettext("ui", "🔴 Высокий")


def format_recent_block_events(
    user: BlockedUser, *, language: Language = Language.RU
) -> str:
    """Format the five latest block events within one embed field."""
    messages = AccessMessages(catalog("access", language))
    events = sorted(
        [(entry.timestamp, "BLOCK", entry) for entry in user.block_history]
        + [(entry.timestamp, "UNBLOCK", entry) for entry in user.unblock_history],
        key=lambda event: event[0],
        reverse=True,
    )[:5]
    lines: list[str] = []
    for timestamp, action, entry in events:
        icon = "🔒" if action == "BLOCK" else "🔓"
        reason = truncate_text(
            entry.reason or messages.catalog.pgettext("ui", "Не указана"),
            width=200,
            mode="middle",
        )
        lines.append(
            messages.event(
                icon=icon,
                action=action,
                timestamp=format_dt(timestamp, "R"),
                admin_id=entry.admin_id,
                reason=reason,
            )
        )
    return truncate_sequence(
        lines,
        max_length=MAX_EMBED_FIELD_LENGTH,
        separator="\n",
        placeholder="...",
    )


def format_name_history(
    history: Sequence[NameHistoryEntry], *, language: Language = Language.RU
) -> str:
    """Format the three latest observed names within one embed field."""
    messages = AccessMessages(catalog("access", language))
    lines: list[str] = []
    for entry in sorted(history, key=lambda entry: entry.timestamp, reverse=True)[:3]:
        timestamp = format_dt(entry.timestamp, "D")
        name = truncate_text(entry.username, width=200)
        lines.append(messages.name_event(timestamp=timestamp, name=name))
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
    language: Language = Language.RU,
) -> str:
    """Build one list entry using current Discord identity when available."""
    messages = AccessMessages(catalog("access", language))
    if member is None:
        user_info = messages.departed(user_id=user_entry.user_id)
        current_username = user_entry.current_username
    else:
        user_info = f"{member.mention} `{member.id}`"
        current_username = member.display_name

    lines = [messages.user(identity=user_info)]
    if not show_details:
        return "\n".join(lines)

    username = truncate_text(current_username, width=80)
    lines.append(messages.current_name(name=username))
    if not user_entry.block_history:
        lines.append(
            messages.catalog.pgettext("ui", "• История блокировок отсутствует.")
        )
        return "\n".join(lines)

    last_block = user_entry.block_history[-1]
    reason = truncate_text(
        last_block.reason or messages.catalog.pgettext("ui", "Не указана"), width=200
    )
    timestamp = format_dt(last_block.timestamp, "R")
    lines.extend(
        [
            messages.last_block(timestamp=timestamp),
            messages.reason(reason=reason),
            messages.admin(admin_id=last_block.admin_id),
        ]
    )
    return "\n".join(lines)


BLOCK = BlockAction.BLOCK
UNBLOCK = BlockAction.UNBLOCK

ACTION_TITLES = {
    "block": pgettext_noop("ui", "Блокировка"),
    "unblock": pgettext_noop("ui", "Разблокировка"),
}
