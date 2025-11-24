"""Administrative commands for user blocking and management.

Provides:
- Blocking/unblocking users from bot access
- Viewing detailed block history
- Listing all blocked users
- Tracking name changes over time

"""

import logging
from enum import StrEnum
from typing import override

import discord
from discord import app_commands
from discord.ext import commands

import config
from api import block_manager
from framework import BaseCog, FeedbackType, FeedbackUI
from resources import ACTION_TITLES


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
    embed = discord.Embed(
        title=title,
        description=description,
        color=config.Color.INFO,
    )

    if reason:
        embed.add_field(name="Причина", value=reason)

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


class AdminCog(BaseCog):
    """Administrative commands for server management.

    Requires administrator permissions for all commands.
    """

    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.logger = logging.getLogger("AdminCog")

    @override
    def should_bypass_block(self, interaction: discord.Interaction) -> bool:
        """Allow admin commands to bypass block checks."""
        return True

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
    ):
        """Block a user from using the bot."""
        guild = await self._require_guild(interaction)
        if block_manager.is_user_blocked(guild.id, user.id):
            await FeedbackUI.send(
                interaction,
                type=FeedbackType.WARNING,
                description=f"{user.mention} уже заблокирован.",
                ephemeral=True,
            )
            return
        block_manager.block_user(guild.id, user, interaction.user.id, reason)
        self.logger.info("Blocked user %d in guild %d", user.id, guild.id)
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
    ):
        """Unblock a user from using the bot."""
        guild = await self._require_guild(interaction)
        if not block_manager.is_user_blocked(guild.id, user.id):
            await FeedbackUI.send(
                interaction,
                type=FeedbackType.WARNING,
                description=f"{user.mention} не заблокирован.",
                ephemeral=True,
            )
            return
        block_manager.unblock_user(guild.id, user, interaction.user.id, reason)
        self.logger.info("Unblocked user %d in guild %d", user.id, guild.id)
        embed = create_block_embed(user, UNBLOCK, reason)
        await FeedbackUI.send(interaction, embed=embed, ephemeral=True)

    @app_commands.command(
        name="blockinfo",
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
    ):
        """Display detailed block history for a user."""
        guild = await self._require_guild(interaction)
        user_entry = block_manager.get_user(guild.id, user.id)

        if not user_entry or not user_entry.block_history:
            self.logger.info(
                f"No block history found for user {user.id} "
                f"in guild {guild.name} ({guild.id})"
            )
            await FeedbackUI.send(
                interaction,
                type=FeedbackType.INFO,
                description=f"{user.mention} не имеет истории блокировок.",
                ephemeral=ephemeral,
            )
            return

        self.logger.info(
            f"Displaying block history for user {user.id} "
            f"in guild {guild.name} ({guild.id})"
        )
        # Build detailed embed
        embed = discord.Embed(
            title="Полная история блокировок",
            color=config.Color.ERROR if user_entry.is_blocked else config.Color.SUCCESS,
        )
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)
        embed.set_thumbnail(url=user.display_avatar.url)

        # Current status
        if user_entry.is_blocked:
            last_block = user_entry.block_history[-1]
            timestamp = int(last_block.timestamp.timestamp())
            status_value = (
                f"**Заблокирован**\n"
                f"• Администратор: <@{last_block.admin_id}>\n"
                f"• Причина: {last_block.reason or 'Не указана'}\n"
                f"• Дата: <t:{timestamp}:F>"
            )
        else:
            status_value = "Не заблокирован"

        embed.add_field(
            name="Текущий статус",
            value=status_value,
            inline=False,
        )

        # Recent events (merge and sort block/unblock history)
        all_events = sorted(
            [(e.timestamp, "BLOCK", e) for e in user_entry.block_history]
            + [(e.timestamp, "UNBLOCK", e) for e in user_entry.unblock_history],
            key=lambda x: x[0],
            reverse=True,
        )[:5]

        if all_events:
            history_lines: list[str] = []
            for timestamp, action, entry in all_events:
                icon = "🔒" if action == "BLOCK" else "🔓"
                ts = int(timestamp.timestamp())
                history_lines.append(
                    f"{icon} **{action}** <t:{ts}:R>\n"
                    f"• Админ: <@{entry.admin_id}>\n"
                    f"• Причина: {entry.reason or 'Не указана'}\n"
                )

            embed.add_field(
                name="Последние события",
                value="\n".join(history_lines)[:1024],
                inline=False,
            )

        # Name history
        if user_entry.name_history:
            name_changes: list[str] = []
            for name_entry in sorted(
                user_entry.name_history,
                key=lambda x: x.timestamp,
                reverse=True,
            )[:3]:
                ts = int(name_entry.timestamp.timestamp())
                name_changes.append(f"<t:{ts}:D>:\n• Имя: {name_entry.username}\n")

            embed.add_field(
                name="История имён",
                value="\n".join(name_changes)[:1024],
            )

        # Statistics
        first_block_ts = int(user_entry.block_history[0].timestamp.timestamp())
        stats = [
            f"• Всего блокировок: {len(user_entry.block_history)}",
            f"• Всего разблокировок: {len(user_entry.unblock_history)}",
            f"• Первая блокировка: <t:{first_block_ts}:D>",
        ]

        if user_entry.unblock_history:
            last_unblock_ts = int(user_entry.unblock_history[-1].timestamp.timestamp())
            stats.append(f"• Последняя разблокировка: <t:{last_unblock_ts}:D>")

        embed.add_field(
            name="Статистика",
            value="\n".join(stats),
            inline=False,
        )

        # Footer with danger level
        danger_level = format_danger_level(len(user_entry.block_history))
        embed.set_footer(text=f"Уровень проблемности: {danger_level}")

        await FeedbackUI.send(interaction, embed=embed, ephemeral=ephemeral)

        self.logger.info(f"Displayed blockinfo for user {user.id} in guild {guild.id}")

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
    ):
        """Display all currently blocked users with basic information."""
        guild = await self._require_guild(interaction)
        all_users = block_manager.get_guild_users(guild.id)
        blocked_users = [u for u in all_users if u.is_blocked]

        if not blocked_users:
            self.logger.info(f"No blocked users found in guild {guild.id}")
            await FeedbackUI.send(
                interaction,
                type=FeedbackType.INFO,
                description="Нет заблокированных пользователей.",
                ephemeral=ephemeral,
            )
            return
        self.logger.info(
            f"Found {len(blocked_users)} blocked users in guild {guild.id} "
        )
        embed = discord.Embed(
            title=f"Заблокированные пользователи ({len(blocked_users)})",
            color=config.Color.INFO,
        )

        unresolved_count = 0
        entries: list[str] = []

        for user_entry in blocked_users:
            user = guild.get_member(user_entry.user_id)
            if user is None:
                user_info = f"Пользователь покинул сервер `{user_entry.user_id}`"
                current_username = user_entry.current_username
                unresolved_count += 1
            else:
                user_info = f"{user.mention} `{user.id}`"
                current_username = user.display_name

            entry = [f"**Пользователь:** {user_info}"]

            if show_details:
                last_block = user_entry.block_history[-1]
                timestamp = int(last_block.timestamp.timestamp())
                entry.extend(
                    [
                        f"• Текущее имя: {current_username}",
                        f"• Последняя блокировка: <t:{timestamp}:R>",
                        f"• Причина: {last_block.reason or 'Не указана'}",
                        f"• Администратор: <@{last_block.admin_id}>",
                    ]
                )

            entries.append("\n".join(entry))

        timestamp = int(blocked_users[0].block_history[-1].timestamp.timestamp())
        embed.description = (
            f"**Статистика блокировок:**\n"
            f"• Всего заблокировано: {len(blocked_users)}\n"
            f"• Не на сервере: {unresolved_count}\n"
            f"• Последняя блокировка: <t:{timestamp}:R>"
        )

        current_field: list[str] = []
        current_length = 0

        for entry in entries:
            entry_length = len(entry) + 2
            if current_length + entry_length > config.MAX_EMBED_FIELD_LENGTH:
                embed.add_field(
                    name="Заблокированные пользователи",
                    value="\n\n".join(current_field),
                    inline=False,
                )
                current_field = []
                current_length = 0
            current_field.append(entry)
            current_length += entry_length

        if current_field:
            embed.add_field(
                name="Заблокированные пользователи"
                if not show_details
                else "Детали блокировок",
                value="\n\n".join(current_field),
                inline=False,
            )

        embed.set_footer(
            text="" if not show_details else "Детальная информация о блокировках"
        )

        await FeedbackUI.send(interaction, embed=embed, ephemeral=ephemeral)


async def setup(bot: commands.Bot):
    """Setup.

    Args:
        bot: BOT ITSELF

    """
    await bot.add_cog(AdminCog(bot))
