"""Birthday commands and daily congratulation orchestration."""

import asyncio
import logging
import secrets
from datetime import date
from typing import TYPE_CHECKING, Literal, Self, override
from uuid import uuid4

import discord
from discord import Interaction, app_commands
from discord.errors import Forbidden, HTTPException
from discord.ext import commands, tasks
from discord.ui import Button
from sqlalchemy.exc import SQLAlchemyError

import config
from api.birthday import (
    BirthdayManager,
    create_birthday_list_embed,
    parse_birthday,
    safe_fetch_member,
)
from api.birthday_models import BirthdayDelivery, BirthdayGuildConfig, BirthdayUser
from framework.authorization import check_component_access
from framework.base_cog import BaseCog
from framework.feedback_ui import FeedbackType, FeedbackUI
from resources import BIRTHDAY_WISHES
from utils.birthday_utils import is_birthday_today
from utils.embeds import SafeEmbed

if TYPE_CHECKING:
    from framework.bot import StupidBot

logger = logging.getLogger(__name__)


async def safe_role_edit(
    member: discord.Member,
    role: discord.Role,
    operation: Literal["add", "remove"],
) -> bool:
    """Safely add or remove a role.

    Args:
        member: Member to modify.
        role: Role to add or remove.
        operation: Requested role operation.

    Returns:
        ``True`` when Discord accepted the edit, otherwise ``False`` for an
        expected permission or request failure.
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

    def __init__(
        self,
        user_id: int,
        guild_id: int,
        manager: BirthdayManager,
        expected_version: int,
    ) -> None:
        super().__init__(timeout=30)
        self.user_id = user_id
        self.expected_version = expected_version
        self.guild_id = guild_id
        self.manager = manager

    @discord.ui.button(label="Да", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: Interaction, _: Button[Self]) -> None:
        if interaction.user.id != self.user_id:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                description="Вы не можете выполнить это действие",
                ephemeral=True,
            )
            return

        if not await check_component_access(interaction):
            return
        try:
            guild_exists, cleared = await self.manager.clear_user_birthday(
                self.guild_id, self.user_id, expected_version=self.expected_version
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
                description=(
                    "Дата уже изменена или удалена. Откройте подтверждение заново."
                ),
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

    def __init__(self, bot: commands.Bot, manager: BirthdayManager) -> None:
        super().__init__(bot)
        self.manager = manager
        self._unavailable_channels: dict[int, int | None] = {}

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
                await self._process_guild(guild_id, today)
            except Exception:
                logger.exception("Failed to process birthdays for guild %s", guild_id)

    @birthday_timer.before_loop
    async def before_birthday_timer(self) -> None:
        await self.bot.wait_until_ready()

    async def _process_guild(self, guild_id: int, today: date) -> None:
        """Process birthday checks for a single server.

        Args:
            guild_id: Guild ID to process
            today: Current date

        """
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        config = await self.manager.get_guild_config(guild_id)
        if not config:
            return

        role = (
            discord.utils.get(guild.roles, id=config.birthday_role_id)
            if config.birthday_role_id
            else None
        )
        await self._reconcile_roles(guild, config, today, role)
        channel = self.bot.get_channel(config.channel_id) if config.channel_id else None
        if not isinstance(channel, discord.TextChannel):
            if (
                guild_id not in self._unavailable_channels
                or self._unavailable_channels[guild_id] != config.channel_id
            ):
                logger.warning(
                    "Birthday notifications unavailable: guild=%s channel=%s; "
                    "retrying each interval",
                    guild_id,
                    config.channel_id,
                )
                self._unavailable_channels[guild_id] = config.channel_id
            return
        if guild_id in self._unavailable_channels:
            self._unavailable_channels.pop(guild_id)
            logger.info(
                "Birthday notification channel recovered: guild=%s channel=%s",
                guild_id,
                config.channel_id,
            )
        birthday_users = config.get_birthdays_today(today)
        for user in birthday_users:
            await self._handle_birthday(guild, channel, user, today, config.version)

    async def _reconcile_roles(
        self,
        guild: discord.Guild,
        config: BirthdayGuildConfig,
        today: date,
        role: discord.Role | None,
    ) -> None:
        """Reconcile role membership independently of sent congratulations."""
        if not role:
            return

        for user_id, user_data in config.users.items():
            try:
                member = await safe_fetch_member(guild, user_id)
                if member is None or not await self.manager.repo.versions_current(
                    guild.id, user_id, config.version, user_data.version
                ):
                    continue
                birthday_today = is_birthday_today(user_data.birthday, today)
                if birthday_today and role not in member.roles:
                    await safe_role_edit(member, role, "add")
                elif not birthday_today and role in member.roles:
                    await safe_role_edit(member, role, "remove")
            except discord.HTTPException as error:
                logger.warning(
                    "Birthday role update failed for guild %s user %s: HTTP %s",
                    guild.id,
                    user_id,
                    error.status,
                )
                logger.debug("Birthday role request traceback", exc_info=True)

    async def _handle_birthday(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        user: BirthdayUser,
        today: date,
        settings_version: int,
    ) -> None:
        """Claim a current birthday before attempting its external delivery."""
        member = await safe_fetch_member(guild, user.user_id)
        if not member:
            return

        claim = BirthdayDelivery(
            uuid4().hex, guild.id, user.user_id, today, settings_version, user.version
        )
        try:
            if not await self.manager.repo.claim_delivery(claim):
                return
            wish = secrets.choice(BIRTHDAY_WISHES or ["С днём рождения!"])
            embed = SafeEmbed(
                title=f"🎉 ПОЗДРАВЛЕНИЯ {user.name}",
                description=f"{wish} {member.mention}",
                color=discord.Color.gold(),
            )
            embed.set_thumbnail(url=config.BOT_ICON)

            if not await self.manager.repo.begin_delivery(claim):
                return
            message = await channel.send(embed=embed)
            await self.manager.repo.finish_delivery(claim, message.id)
            logger.info(
                "Birthday delivered: guild=%s user=%s operation=%s message=%s",
                guild.id,
                user.user_id,
                claim.operation_id,
                message.id,
            )

        except Exception:
            logger.exception(
                "Birthday delivery failed: guild=%s user=%s channel=%s "
                "operation=%s date=%s",
                guild.id,
                user.user_id,
                channel.id,
                claim.operation_id,
                today,
            )
        finally:
            try:
                await self.manager.repo.release_delivery(claim)
            except Exception:
                logger.exception(
                    "Failed to release birthday claim: guild=%s user=%s operation=%s",
                    guild.id,
                    user.user_id,
                    claim.operation_id,
                )

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
        config = await self.manager.get_guild_config(guild.id)

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
        config = await self.manager.get_guild_config(guild.id)
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


async def setup(bot: "StupidBot") -> None:
    """Register the birthday cog."""
    await bot.add_cog(BirthdayCog(bot, bot.birthday_manager))
