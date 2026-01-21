"""Administrative commands for user blocking and management.

Provides:
- Blocking/unblocking users from bot access
- Viewing detailed block history
- Listing all blocked users
- Tracking name changes over time

"""

import logging
from enum import StrEnum
from typing import NoReturn, override

import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import format_dt

import config
from api import block_manager
from framework import BaseCog, FeedbackType, FeedbackUI, handle_errors, is_owner_app
from resources import ACTION_TITLES
from utils import SafeEmbed, truncate_sequence, truncate_text

logger = logging.getLogger(__name__)


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


class AdminCog(BaseCog):
    """Administrative commands for server management.

    Requires administrator permissions for all commands.
    """

    def __init__(self, bot: commands.Bot):
        super().__init__(bot)

    @override
    def should_bypass_block(self, interaction: discord.Interaction) -> bool:
        """Allow admin commands to bypass block checks."""
        return True

    @app_commands.command(name="error-test", description="Тестирование ошибок")
    @is_owner_app()
    @app_commands.default_permissions(administrator=True)
    async def error(self, _: discord.Interaction) -> NoReturn:
        raise RuntimeError("Test error")

    @app_commands.command(name="error-test-handled", description="Тестирование ошибок")
    @is_owner_app()
    @app_commands.default_permissions(administrator=True)
    @handle_errors()
    async def error_handled(self, _: discord.Interaction) -> NoReturn:
        raise RuntimeError("Test handled error")

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
        if await block_manager.is_user_blocked(guild.id, user.id):
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description=f"{user.mention} уже заблокирован.",
                ephemeral=True,
            )
            return
        await block_manager.block_user(guild.id, user, interaction.user.id, reason)
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
    ):
        """Unblock a user from using the bot."""
        guild = await self._require_guild(interaction)
        if not await block_manager.is_user_blocked(guild.id, user.id):
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description=f"{user.mention} не заблокирован.",
                ephemeral=True,
            )
            return
        await block_manager.unblock_user(guild.id, user, interaction.user.id, reason)
        logger.info("Unblocked user %d in guild %d", user.id, guild.id)
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
        user_entry = await block_manager.get_user(guild.id, user.id)

        if not user_entry or not user_entry.block_history:
            logger.info(
                f"No block history found for user {user.id} "
                + f"in guild {guild.name} ({guild.id})"
            )
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.INFO,
                description=f"{user.mention} не имеет истории блокировок.",
                ephemeral=ephemeral,
            )
            return

        logger.info(
            f"Displaying block history for user {user.id} "
            + f"in guild {guild.name} ({guild.id})"
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
                f"• Дата: {timestamp}"
                f"• Причина: {last_block.reason or 'Не указана'}\n"
            )
        else:
            status_value = "Не заблокирован"

        embed.safe_add_field(
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
                icon = ("🔓", "🔒")[action == "BLOCK"]
                truncated_reason = truncate_text(
                    entry.reason or "Не указана", width=200, mode="middle"
                )
                history_lines.append(
                    f"{icon} **{action}** {format_dt(timestamp, 'R')}\n"
                    + f"• Админ: <@{entry.admin_id}>\n"
                    + f"• Причина: {truncated_reason}"
                )

            history_value = truncate_sequence(
                history_lines,
                max_length=config.MAX_EMBED_FIELD_LENGTH,
                separator="\n",
                placeholder="...",
            )
            embed.safe_add_field(
                name="Последние события",
                value=history_value,
                inline=False,
            )

        # Name history
        if user_entry.name_history[:21]:
            name_changes: list[str] = []
            for name_entry in sorted(
                user_entry.name_history,
                key=lambda x: x.timestamp,
                reverse=True,
            )[:3]:
                ts = format_dt(name_entry.timestamp, "D")
                username_text = truncate_text(name_entry.username, width=200)
                name_changes.append(f"{ts}:\n• Имя: {username_text}")

            names_value = truncate_sequence(
                name_changes,
                max_length=config.MAX_EMBED_FIELD_LENGTH,
                separator="\n",
                placeholder="...",
            )
            embed.safe_add_field(
                name="История имён",
                value=names_value,
            )

        # Statistics
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

        # Footer with danger level
        danger_level = format_danger_level(len(user_entry.block_history))
        embed.set_footer(text=f"Уровень проблемности: {danger_level}")

        await FeedbackUI.send(interaction, embed=embed, ephemeral=ephemeral)

        logger.info(f"Displayed blockinfo for user {user.id} in guild {guild.id}")

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
        all_users = await block_manager.get_guild_users(guild.id)
        blocked_users = [u for u in all_users if u.is_blocked]

        if not blocked_users:
            logger.info(f"No blocked users found in guild {guild.id}")
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.INFO,
                description="Нет заблокированных пользователей.",
                ephemeral=ephemeral,
            )
            return
        logger.info(f"Found {len(blocked_users)} blocked users in guild {guild.id} ")
        embed = SafeEmbed(
            title=f"Заблокированные пользователи ({len(blocked_users)})",
            color=config.Color.INFO,
        )

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
        embed.add_field_pages(
            name="Заблокированные пользователи",
            lines=entries,
            page_size=15,
            separator="\n",
        )

        embed.set_footer(
            text="" if not show_details else "Детальная информация о блокировках"
        )

        await FeedbackUI.send(interaction, embed=embed, ephemeral=ephemeral)

    @app_commands.command(
        name="del", description="Удалить сообщение по ID (только владелец)."
    )
    @is_owner_app()
    @app_commands.describe(message_id="ID сообщения для удаления")
    @app_commands.default_permissions(administrator=True)
    async def delete_message(self, interaction: discord.Interaction, message_id: str):
        """Silently deletes a message by ID."""
        try:
            channel = interaction.channel
            if channel is None or not isinstance(channel, discord.abc.Messageable):
                await interaction.response.send_message(
                    "Невозможно удалить сообщение в этом канале.", ephemeral=True
                )
                return
            msg = await channel.fetch_message(int(message_id))

            await msg.delete()
            await interaction.response.send_message(
                "Удалено.", ephemeral=True, delete_after=1.0
            )
        except (discord.NotFound, ValueError):
            await interaction.response.send_message(
                "Сообщение не найдено.", ephemeral=True
            )
        except Exception:
            await interaction.response.send_message("Нет прав.", ephemeral=True)


async def setup(bot: commands.Bot):
    """Setup.

    Args:
        bot: BOT ITSELF

    """
    await bot.add_cog(AdminCog(bot))
