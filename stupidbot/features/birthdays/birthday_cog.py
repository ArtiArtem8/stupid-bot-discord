"""Birthday commands and daily congratulation orchestration."""

import asyncio
import logging
from datetime import date
from typing import TYPE_CHECKING, override

import discord
from discord import Interaction, app_commands
from discord.ext import commands, tasks
from sqlalchemy.exc import SQLAlchemyError

from stupidbot import settings as config
from stupidbot.discord_ui.base_cog import BaseCog
from stupidbot.discord_ui.feedback import FeedbackType, FeedbackUI
from stupidbot.features.birthdays.dates import parse_birthday
from stupidbot.features.birthdays.delivery import BirthdayDeliveryService
from stupidbot.features.birthdays.presentation import (
    build_birthday_list,
    create_birthday_list_embed,
)
from stupidbot.features.birthdays.repository import BirthdayRepository
from stupidbot.features.birthdays.views import ConfirmDeleteView

if TYPE_CHECKING:
    from stupidbot.app.bot import StupidBot

logger = logging.getLogger(__name__)


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

    def __init__(self, bot: commands.Bot, manager: BirthdayRepository) -> None:
        super().__init__(bot)
        self.manager = manager
        self.delivery = BirthdayDeliveryService(bot, manager)

    @override
    async def cog_load(self) -> None:
        """Start birthday checks after the cog is registered."""
        if not self.birthday_timer.is_running():
            self.birthday_timer.start()

    @override
    async def cog_unload(self) -> None:
        self.birthday_timer.cancel()
        if task := self.birthday_timer.get_task():
            await asyncio.gather(task, return_exceptions=True)

    @tasks.loop(seconds=config.BIRTHDAY_CHECK_INTERVAL)
    async def birthday_timer(self) -> None:
        """Check registered birthdays and deliver due congratulations."""
        today = date.today()
        try:
            guild_ids = await self.manager.get_all_guild_ids()
        except SQLAlchemyError:
            logger.exception("Failed to list birthday guilds; retrying next interval")
            return
        for guild_id in guild_ids:
            try:
                await self.delivery.process_guild(guild_id, today)
            except Exception:
                logger.exception("Failed to process birthdays for guild %s", guild_id)

    @birthday_timer.before_loop
    async def before_birthday_timer(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="set-birthday",
        description="Установить свой день рождения (формат: ДД-ММ-ГГГГ или ГГГГ-ММ-ДД)",
    )
    @app_commands.describe(
        date_input="Дата рождения (например: 15-05-2000 или 2000-05-15)"
    )
    @app_commands.guild_only()
    async def set_birthday(self, interaction: Interaction, date_input: str) -> None:
        try:
            normalized_date = parse_birthday(date_input)
        except ValueError:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description="Неверный формат даты. Используйте ДД-ММ-ГГГГ / ГГГГ-ММ-ДД",
                ephemeral=True,
            )
            return
        guild = await self._require_guild(interaction)
        await self.manager.set_user_birthday(
            guild_id=guild.id,
            server_name=guild.name,
            channel_id=interaction.channel_id,
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
    ) -> None:
        guild = await self._require_guild(interaction)

        await self.manager.configure_guild(
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

    @app_commands.command(
        name="remove-birthday", description="Удалить свой день рождения из системы"
    )
    @app_commands.guild_only()
    async def remove_birthday(self, interaction: Interaction) -> None:
        guild = await self._require_guild(interaction)
        config = await self.manager.get(guild.id)

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

        view = ConfirmDeleteView(
            interaction.user.id, guild.id, self.manager, user.version
        )
        msg = "Вы уверены, что хотите удалить свой день рождения?"
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.WARNING,
            description=msg,
            view=view,
            ephemeral=True,
        )

    @app_commands.command(
        name="list-birthdays",
        description="Список дней рождений на сервере, отсортированные по ближайшим",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(ephemeral="Скрыть сообщение после выполнения")
    async def list_birthdays(
        self, interaction: Interaction, ephemeral: bool = True
    ) -> None:
        guild = await self._require_guild(interaction)
        config = await self.manager.get(guild.id)
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

        entries = build_birthday_list(
            config, {member.id: member.display_name for member in guild.members}, today
        )

        if not entries:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description="На этом сервере нет сохранённых дней рождений.",
                ephemeral=True,
            )
            return

        embed = create_birthday_list_embed(guild.name, entries)
        await FeedbackUI.send(interaction, embed=embed, ephemeral=ephemeral)


async def setup(bot: "StupidBot") -> None:
    """Register the birthday cog."""
    await bot.add_cog(BirthdayCog(bot, bot.birthday_repository))
