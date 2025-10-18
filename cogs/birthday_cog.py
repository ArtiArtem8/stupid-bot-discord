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
from typing import Any, Literal

import discord
from discord import Button, Interaction, app_commands
from discord.errors import Forbidden, HTTPException
from discord.ext import commands, tasks

from config import BIRTHDAY_CHECK_INTERVAL, BIRTHDAY_WISHES, BOT_ICON
from utils import (
    BaseCog,
    BirthdayGuildConfig,
    BirthdayUser,
    birthday_manager,
    create_birthday_list_embed,
    parse_birthday,
    safe_fetch_member,
)

DATE_FORMAT = "%d-%m-%Y"
"""canonical format: DD-MM-YYYY"""

BirthdayData = dict[str, Any]
GuildData = dict[str, Any]


async def safe_role_edit(
    member: discord.Member,
    role: discord.Role,
    operation: Literal["add", "remove"],
    logger: logging.Logger,
) -> bool:
    """Safely add or remove role with permission checks.

    Args:
        member: Member to modify
        role: Role to add/remove
        operation: Either "add" or "remove"
        logger: Logger for warnings

    Returns:
        True if successful, False otherwise

    """
    if not member.guild.me.guild_permissions.manage_roles:
        logger.warning("Bot lacks manage_roles permission")
        return False

    if role >= member.guild.me.top_role:
        logger.debug("Role too high, %s >= %s", role, member.guild.me.top_role)
        return False

    if not role.is_assignable():
        logger.debug("Role is not assignable, %s", role)
        return False

    try:
        match operation:
            case "add":
                await member.add_roles(role, reason="День рождения")
            case "remove":
                await member.remove_roles(role, reason="День рождения прошел")
            case _:
                raise ValueError(f"Invalid operation: {operation}")  # !unreachable
        return True
    except Forbidden:
        return False
    except HTTPException as exc:
        if exc.status in (400, 403, 404):
            return False
        raise


class ConfirmDeleteView(discord.ui.View):
    """Confirmation view for birthday deletion."""

    def __init__(self, user_id: int, guild_id: int) -> None:
        super().__init__(timeout=30)
        self.user_id = user_id
        self.guild_id = guild_id

    @discord.ui.button(label="Да", style=discord.ButtonStyle.green)  # type: ignore
    async def confirm(self, interaction: Interaction, button: Button) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Вы не можете выполнить это действие", ephemeral=True
            )
            return

        config = birthday_manager.get_guild_config(self.guild_id)
        if not config:
            await interaction.response.edit_message(
                content="Конфигурация сервера не найдена", view=None
            )
            return
        user = config.get_user(self.user_id)
        if not user or not user.has_birthday():
            await interaction.response.edit_message(
                content="У вас нет сохранённого дня рождения.", view=None
            )
            return

        user.clear_birthday()
        try:
            birthday_manager.save_guild_config(config)
            await interaction.response.edit_message(
                content="Ваш день рождения удалён", view=None
            )
        except Exception as e:
            logging.getLogger("BirthdayCog").error(
                "Error saving birthday file after deletion: %s", e
            )
            await interaction.response.edit_message(content="Ошибка записи", view=None)

    @discord.ui.button(label="Нет", style=discord.ButtonStyle.red)  # type: ignore
    async def cancel(self, interaction: Interaction, button: Button) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Вы не можете выполнить это действие.", ephemeral=True
            )
            return
        await interaction.response.edit_message(content="Отменено", view=None)


class BirthdayCog(BaseCog):
    """Cog for birthday management and automatic congratulations.

    Features:
    - User birthday registration
    - Automatic daily birthday checks
    - Birthday role management
    - Birthday list display

    Configuration:
        Set BIRTHDAY_CHECK_INTERVAL in config for check frequency (seconds)
    """

    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.logger = logging.getLogger("BirthdayCog")
        self.birthday_timer.start()

    async def cog_unload(self):
        self.birthday_timer.cancel()

    @tasks.loop(seconds=BIRTHDAY_CHECK_INTERVAL)
    async def birthday_timer(self):
        """Main timer loop for birthday checks."""
        today = date.today()
        for guild_id in birthday_manager.get_all_guild_ids():
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

        config = birthday_manager.get_guild_config(guild_id)
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
            await self._handle_birthday(guild, channel, role, user, today, config)

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
            if user_data.birth_day_month != today_key:
                member = await safe_fetch_member(guild, user_id, self.logger)
                if member and role in member.roles:
                    await safe_role_edit(member, role, "remove", self.logger)

    async def _handle_birthday(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        role: discord.Role | None,
        user: BirthdayUser,
        today: date,
        config: BirthdayGuildConfig,
    ) -> None:
        """Handle birthday congratulations and role assignment.

        Args:
            guild: Guild context
            channel: Channel for messages
            role: Optional birthday role
            user: User with birthday
            today: Current date
            config: Guild configuration

        """
        member = await safe_fetch_member(guild, user.user_id, self.logger)
        if not member:
            return

        try:
            if role and role not in member.roles:
                await safe_role_edit(member, role, "add", self.logger)

            wish = secrets.choice(BIRTHDAY_WISHES or ["С днём рождения!"])
            embed = discord.Embed(
                title=f"🎉 ПОЗДРАВЛЕНИЯ {user.name}",
                description=f"{wish} {member.mention}",
                color=discord.Color.gold(),
            )
            embed.set_thumbnail(url=BOT_ICON)

            await channel.send(embed=embed)

            user.add_congratulation(today)
            birthday_manager.save_guild_config(config)

        except Exception as e:
            self.logger.error(f"Error handling birthday for {user.user_id}: {e}")

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
            return await interaction.response.send_message(
                "Неверный формат даты. Используйте ДД-ММ-ГГГГ или ГГГГ-ММ-ДД",
                ephemeral=True,
            )
        guild = await self._require_guild(interaction)
        config = birthday_manager.get_or_create_guild_config(
            guild_id=guild.id,
            server_name=guild.name,
            channel_id=interaction.channel_id or 0,
        )
        user = config.get_or_create_user(
            user_id=interaction.user.id,
            name=interaction.user.name,
        )
        user.birthday = normalized_date
        try:
            birthday_manager.save_guild_config(config)
            msg = f"Ваш день рождения записан: {normalized_date}"
            await interaction.response.send_message(msg, ephemeral=True)
        except Exception as e:
            self.logger.error("Error saving birthday: %s", e)
            await interaction.response.send_message(
                "Ошибка сохранения данных.", ephemeral=True
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

        config = birthday_manager.get_or_create_guild_config(
            guild_id=guild.id,
            server_name=guild.name,
            channel_id=channel.id,
        )
        config.channel_id = channel.id
        config.birthday_role_id = role.id if role else None
        try:
            birthday_manager.save_guild_config(config)
            response: str = f"Настройки обновлены:\n- Канал: {channel.mention}"
            if role:
                response += f"\n- Роль: {role.mention}"
            await interaction.response.send_message(response, ephemeral=True)
        except Exception as e:
            self.logger.error("Error saving configuration: %s", e)
            await interaction.response.send_message(
                "Ошибка сохранения настроек", ephemeral=True
            )

    @app_commands.command(
        name="remove-birthday", description="Удалить свой день рождения из системы"
    )
    @app_commands.guild_only()
    async def remove_birthday(self, interaction: Interaction):
        """Remove your birthday from the system."""
        guild = await self._require_guild(interaction)
        config = birthday_manager.get_guild_config(guild.id)

        if not config:
            await interaction.response.send_message(
                "Конфигурация сервера не найдена", ephemeral=True
            )
            return

        user = config.get_user(interaction.user.id)
        if not user or not user.has_birthday():
            await interaction.response.send_message(
                "Вы не установили свой день рождения", ephemeral=True
            )
            return

        view = ConfirmDeleteView(interaction.user.id, guild.id)
        msg = "❓ Вы уверены, что хотите удалить свой день рождения?"
        await interaction.response.send_message(msg, view=view, ephemeral=True)

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
        config = birthday_manager.get_guild_config(guild.id)
        if not config:
            await interaction.response.send_message(
                "На этом сервере нет настроенной системы дней рождений",
                ephemeral=True,
            )
            return

        if not config.users:
            await interaction.response.send_message(
                "На этом сервере нет сохранённых дней рождений.", ephemeral=True
            )
            return

        today = date.today()

        entries = await config.get_sorted_birthday_list(
            guild=guild, reference_date=today, logger=self.logger
        )

        if not entries:
            await interaction.response.send_message(
                "На этом сервере нет **корректно** сохранённых дней рождений.",
                ephemeral=True,
            )

        embed = create_birthday_list_embed(guild.name, entries)
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)


async def setup(bot: commands.Bot):
    """Setup.

    Args:
        bot: BOT ITSELF

    """
    await bot.add_cog(BirthdayCog(bot))
