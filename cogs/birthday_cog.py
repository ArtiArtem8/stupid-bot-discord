"""Birthday management system with automatic congratulations.

Provides:
- Birthday registration and removal
- Automatic daily checks and congratulations
- Birthday role management
- Birthday list viewing with sorting

Configuration:
    Requires BIRTHDAY_FILE, BIRTHDAY_CHECK_INTERVAL in config.py
"""

import logging
import secrets
from datetime import date
from typing import Literal, Self, override

import discord
from discord import Interaction, app_commands
from discord.errors import Forbidden, HTTPException
from discord.ext import commands, tasks
from discord.ui import Button

import config
from api import (
    BirthdayGuildConfig,
    BirthdayUser,
    birthday_manager,
    create_birthday_list_embed,
    parse_birthday,
    safe_fetch_member,
)
from framework import BaseCog, FeedbackType, FeedbackUI
from resources import BIRTHDAY_WISHES
from utils import SafeEmbed

logger = logging.getLogger(__name__)


async def safe_role_edit(
    member: discord.Member,
    role: discord.Role,
    operation: Literal["add", "remove"],
) -> bool:
    """Safely add or remove a role.

    Args:
        member: Member to modify
        role: Role to add/remove
        operation: Either "add" or "remove"
        logger: Logger for warnings

    Returns:
        True if successful, False otherwise

    """
    try:
        match operation:
            case "add":
                await member.add_roles(role, reason="День рождения")
            case "remove":
                await member.remove_roles(role, reason="День рождения прошел")
        return True

    except Forbidden:
        logger.debug(
            "Forbidden: Cannot %s role %s for %s (check permission and role hierarchy)",
            operation,
            role.name,
            member,
        )
        return False

    except HTTPException as exc:
        if exc.status in (400, 403, 404):
            logger.debug(
                "HTTP %s when attempting to %s role %s for %s: %s",
                exc.status,
                operation,
                role.name,
                member,
                exc.text,
            )
            return False
        raise


class ConfirmDeleteView(discord.ui.View):
    """Confirmation view for birthday deletion."""

    def __init__(self, user_id: int, guild_id: int) -> None:
        super().__init__(timeout=30)
        self.user_id = user_id
        self.guild_id = guild_id

    @discord.ui.button(label="Да", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: Interaction, _: Button[Self]):
        if interaction.user.id != self.user_id:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                description="Вы не можете выполнить это действие",
                ephemeral=True,
            )
            return

        try:
            guild_exists, cleared = await birthday_manager.clear_user_birthday(
                self.guild_id, self.user_id
            )
        except Exception:
            logger.exception(
                "Failed to save birthday removal for user %s", self.user_id
            )
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                title="Ошибка",
                description="Произошла ошибка при удалении дня рождения",
                ephemeral=True,
            )
            return
        if not guild_exists:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                description="Конфигурация сервера не найдена",
                ephemeral=True,
            )
            return
        if not cleared:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description="У вас нет сохранённого дня рождения.",
                ephemeral=True,
            )
            return
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.SUCCESS,
            description="Ваш день рождения удалён",
            ephemeral=True,
        )

    @discord.ui.button(label="Нет", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: Interaction, _: Button[Self]) -> None:
        if interaction.user.id != self.user_id:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                description="Вы не можете выполнить это действие.",
                ephemeral=True,
            )
            return
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.INFO,
            description="Отменено",
            ephemeral=True,
        )


class BirthdayCog(BaseCog):
    """Cog for birthday management and automatic congratulations.

    Features:
    - User birthday registration
    - Automatic daily checks
    - Birthday role management
    - Birthday list display

    Configuration:
        Set BIRTHDAY_CHECK_INTERVAL in config for check frequency (seconds)
    """

    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.birthday_timer.start()

    @override
    async def cog_unload(self):
        self.birthday_timer.cancel()

    @tasks.loop(seconds=config.BIRTHDAY_CHECK_INTERVAL)
    async def birthday_timer(self):
        """Main timer loop for birthday checks."""
        today = date.today()
        guild_ids = await birthday_manager.get_all_guild_ids()
        for guild_id in guild_ids:
            await self._process_guild(guild_id, today)

    @birthday_timer.before_loop
    async def before_birthday_timer(self):
        await self.bot.wait_until_ready()

    async def _process_guild(self, guild_id: int, today: date):
        """Process birthday checks for a single server.

        Args:
            guild_id: Guild ID to process
            today: Current date

        """
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        config = await birthday_manager.get_guild_config(guild_id)
        if not config:
            return

        channel = self.bot.get_channel(config.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        role = (
            discord.utils.get(guild.roles, id=config.birthday_role_id)
            if config.birthday_role_id
            else None
        )
        birthday_users = config.get_birthdays_today(today)
        await self._cleanup_roles(guild, config, today, role)
        for user in birthday_users:
            await self._handle_birthday(guild, channel, role, user, today)

    async def _cleanup_roles(
        self,
        guild: discord.Guild,
        config: BirthdayGuildConfig,
        today: date,
        role: discord.Role | None,
    ) -> None:
        """Remove birthday role from users whose birthday is not today.

        Args:
            guild: Guild context
            config: Guild birthday configuration
            today: Current date
            role: Birthday role to manage

        """
        if not role:
            return

        today_key = today.strftime("%d-%m")

        for user_id, user_data in config.users.items():
            if user_data.birth_day_month() != today_key:
                member = await safe_fetch_member(guild, user_id)
                if member and role in member.roles:
                    await safe_role_edit(member, role, "remove")

    async def _handle_birthday(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        role: discord.Role | None,
        user: BirthdayUser,
        today: date,
    ) -> None:
        """Handle birthday congratulations and role assignment.

        Args:
            guild: Guild context
            channel: Channel for messages
            role: Optional birthday role
            user: User with birthday
            today: Current date

        """
        member = await safe_fetch_member(guild, user.user_id)
        if not member:
            return

        try:
            if role and role not in member.roles:
                await safe_role_edit(member, role, "add")

            wish = secrets.choice(BIRTHDAY_WISHES or ["С днём рождения!"])
            embed = SafeEmbed(
                title=f"🎉 ПОЗДРАВЛЕНИЯ {user.name}",
                description=f"{wish} {member.mention}",
                color=discord.Color.gold(),
            )
            embed.set_thumbnail(url=config.BOT_ICON)

            await channel.send(embed=embed)

            await birthday_manager.record_congratulation(guild.id, user.user_id, today)

        except Exception:
            logger.exception("Failed to handle birthday for user %s", user.user_id)

    @app_commands.command(
        name="setbirthday",
        description="Установить свой день рождения (формат: ДД-ММ-ГГГГ или ГГГГ-ММ-ДД)",
    )
    @app_commands.describe(
        date_input="Дата рождения (например: 15-05-2000 или 2000-05-15)"
    )
    @app_commands.guild_only()
    async def set_birthday(self, interaction: Interaction, date_input: str):
        """Set your birthday in the system.

        Args:
            interaction: Command interaction
            date_input: Birthday date string

        Examples:
            /setbirthday 15-05-2000
            /setbirthday 2000-05-15

        """
        try:
            normalized_date = parse_birthday(date_input)
        except ValueError:
            return await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description="Неверный формат даты. Используйте ДД-ММ-ГГГГ / ГГГГ-ММ-ДД",
                ephemeral=True,
            )
        guild = await self._require_guild(interaction)
        try:
            await birthday_manager.set_user_birthday(
                guild_id=guild.id,
                server_name=guild.name,
                channel_id=interaction.channel_id or 0,
                user_id=interaction.user.id,
                user_name=interaction.user.name,
                birthday=normalized_date,
            )
            msg = f"Ваш день рождения записан: {normalized_date}"
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.SUCCESS,
                description=msg,
                ephemeral=True,
            )
        except Exception:
            logger.exception("Failed to save birthday for user %s", interaction.user.id)

            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                title="Ошибка",
                description="Произошла ошибка сохранения данных.",
                ephemeral=True,
            )

    @app_commands.command(
        name="setup-birthdays",
        description="Настроить систему дней рождений для сервера",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    @app_commands.describe(
        channel="Канал для поздравлений", role="Роль для именинников (опционально)"
    )
    async def setup_birthdays(
        self,
        interaction: Interaction,
        channel: discord.TextChannel,
        role: discord.Role | None = None,
    ):
        """Configure birthday system for the server."""
        guild = await self._require_guild(interaction)

        try:
            await birthday_manager.configure_guild(
                guild_id=guild.id,
                server_name=guild.name,
                channel_id=channel.id,
                birthday_role_id=role.id if role else None,
            )
            response: str = f"Настройки обновлены:\n- Канал: {channel.mention}"
            if role:
                response += f"\n- Роль: {role.mention}"
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.SUCCESS,
                description=response,
                ephemeral=True,
            )
        except Exception:
            logger.exception(
                "Failed to save birthday configuration for guild %s", guild.id
            )
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                title="Ошибка",
                description="Произошла ошибка сохранения данных.",
                ephemeral=True,
            )

    @app_commands.command(
        name="remove-birthday", description="Удалить свой день рождения из системы"
    )
    @app_commands.guild_only()
    async def remove_birthday(self, interaction: Interaction):
        """Remove your birthday from the system."""
        guild = await self._require_guild(interaction)
        config = await birthday_manager.get_guild_config(guild.id)

        if not config:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                description="Конфигурация сервера не найдена",
                ephemeral=True,
            )
            return

        user = config.get_user(interaction.user.id)
        if not user or not user.has_birthday():
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description="Вы не установили свой день рождения",
                ephemeral=True,
            )
            return

        view = ConfirmDeleteView(interaction.user.id, guild.id)
        msg = "Вы уверены, что хотите удалить свой день рождения?"
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.WARNING,
            description=msg,
            view=view,
            ephemeral=True,
        )

    @app_commands.command(
        name="list_birthdays",
        description="Список дней рождений на сервере, отсортированные по ближайшим",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(ephemeral="Скрыть сообщение после выполнения")
    async def list_birthdays(self, interaction: Interaction, ephemeral: bool = True):
        """Display all birthdays in the guild, sorted by closest to today."""
        guild = await self._require_guild(interaction)
        config = await birthday_manager.get_guild_config(guild.id)
        if not config:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description="На этом сервере нет настроенной системы дней рождений",
                ephemeral=True,
            )
            return

        if not config.users:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.INFO,
                description="На этом сервере нет сохранённых дней рождений.",
                ephemeral=True,
            )
            return

        today = date.today()

        entries = await config.get_sorted_birthday_list(
            guild=guild, reference_date=today, logger=logger
        )

        if not entries:
            msg = "На этом сервере нет **корректно** сохранённых дней рождений."
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description=msg,
                ephemeral=True,
            )

        embed = create_birthday_list_embed(guild.name, entries)
        await FeedbackUI.send(interaction, embed=embed, ephemeral=ephemeral)


async def setup(bot: commands.Bot):
    """Setup.

    Args:
        bot: BOT ITSELF

    """
    await bot.add_cog(BirthdayCog(bot))
