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
from stupidbot.app.health import ComponentHealth, TaskHealth
from stupidbot.discord_ui.base_cog import BaseCog
from stupidbot.discord_ui.feedback import FeedbackType, FeedbackUI
from stupidbot.features.birthdays.dates import parse_birthday
from stupidbot.features.birthdays.delivery import BirthdayDeliveryService
from stupidbot.features.birthdays.messages import BirthdayMessages
from stupidbot.features.birthdays.presentation import (
    build_birthday_list,
    create_birthday_list_embed,
)
from stupidbot.features.birthdays.repository import BirthdayRepository
from stupidbot.features.birthdays.views import ConfirmDeleteView
from stupidbot.i18n.catalog import catalog
from stupidbot.i18n.discord import interaction_context

if TYPE_CHECKING:
    from stupidbot.app.bot import StupidBot

logger = logging.getLogger(__name__)


class BirthdayCog(BaseCog):
    """Manage saved birthdays and own the periodic role and delivery checks."""

    def __init__(self, bot: commands.Bot, manager: BirthdayRepository) -> None:
        super().__init__(bot)
        self.manager = manager
        self.delivery = BirthdayDeliveryService(bot, manager)

    @property
    def runtime_health(self) -> ComponentHealth:
        """Expose the daily delivery loop without restarting failed work."""
        loop = self.birthday_timer
        return ComponentHealth(
            loop.is_running(),
            (TaskHealth("birthday_timer", loop.is_running(), loop.failed()),),
        )

    @override
    def command_response_ephemeral(self, _interaction: Interaction) -> bool:
        options: dict[str, object] = dict(iter(_interaction.namespace))
        return options.get("ephemeral", True) is not False

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
        description=app_commands.locale_str(
            "Set your birthday (DD-MM-YYYY or YYYY-MM-DD)",
            domain="birthdays",
        ),
    )
    @app_commands.describe(
        date_input=app_commands.locale_str(
            "Birthday (e.g. 15-05-2000 or 2000-05-15)", domain="birthdays"
        )
    )
    @app_commands.guild_only()
    async def set_birthday(self, interaction: Interaction, date_input: str) -> None:
        context = await interaction_context(interaction, private=True)
        messages = BirthdayMessages(catalog("birthdays", context.language))
        try:
            normalized_date = parse_birthday(date_input)
        except ValueError:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description=messages.catalog.pgettext(
                    "ui", "Invalid date format. Use DD-MM-YYYY / YYYY-MM-DD"
                ),
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
        msg = messages.saved(date=normalized_date)
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.SUCCESS,
            description=msg,
            ephemeral=True,
        )

    @app_commands.command(
        name="setup-birthdays",
        description=app_commands.locale_str(
            "Configure birthdays for this server", domain="birthdays"
        ),
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    @app_commands.describe(
        channel=app_commands.locale_str(
            "Channel for birthday wishes", domain="birthdays"
        ),
        role=app_commands.locale_str("Birthday role (optional)", domain="birthdays"),
    )
    async def setup_birthdays(
        self,
        interaction: Interaction,
        channel: discord.TextChannel,
        role: discord.Role | None = None,
    ) -> None:
        context = await interaction_context(interaction, private=True)
        messages = BirthdayMessages(catalog("birthdays", context.language))
        guild = await self._require_guild(interaction)

        await self.manager.configure_guild(
            guild_id=guild.id,
            server_name=guild.name,
            channel_id=channel.id,
            birthday_role_id=role.id if role else None,
        )
        response: str = messages.channel(mention=channel.mention)
        if role:
            response += messages.role(mention=role.mention)
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.SUCCESS,
            description=response,
            ephemeral=True,
        )

    @app_commands.command(
        name="remove-birthday",
        description=app_commands.locale_str(
            "Remove your saved birthday", domain="birthdays"
        ),
    )
    @app_commands.guild_only()
    async def remove_birthday(self, interaction: Interaction) -> None:
        context = await interaction_context(interaction, private=True)
        messages = BirthdayMessages(catalog("birthdays", context.language))
        guild = await self._require_guild(interaction)
        config = await self.manager.get(guild.id)

        if not config:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                description=messages.catalog.pgettext(
                    "ui", "Server configuration not found"
                ),
                ephemeral=True,
            )
            return

        user = config.get_user(interaction.user.id)
        if not user or not user.has_birthday():
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description=messages.catalog.pgettext(
                    "ui", "You have not set your birthday"
                ),
                ephemeral=True,
            )
            return

        view = ConfirmDeleteView(
            interaction.user.id, guild.id, self.manager, user.version, context=context
        )
        msg = messages.catalog.pgettext(
            "ui", "Are you sure you want to remove your birthday?"
        )
        view.message = await interaction.edit_original_response(
            embed=FeedbackUI.make_embed(None, msg, FeedbackType.WARNING), view=view
        )

    @app_commands.command(
        name="list-birthdays",
        description=app_commands.locale_str(
            "List server birthdays, with the nearest first",
            domain="birthdays",
        ),
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        ephemeral=app_commands.locale_str(
            "Show the response only to you", domain="birthdays"
        )
    )
    async def list_birthdays(
        self, interaction: Interaction, ephemeral: bool = True
    ) -> None:
        context = await interaction_context(interaction, private=True)
        messages = BirthdayMessages(catalog("birthdays", context.language))
        guild = await self._require_guild(interaction)
        config = await self.manager.get(guild.id)
        if not config:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description=messages.catalog.pgettext(
                    "ui", "Birthdays have not been configured on this server"
                ),
                ephemeral=True,
            )
            return

        if not config.users:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.INFO,
                description=messages.catalog.pgettext(
                    "ui", "There are no saved birthdays on this server."
                ),
                ephemeral=True,
            )
            return

        today = date.today()

        context = await interaction_context(interaction, private=ephemeral)
        entries = build_birthday_list(
            config,
            {member.id: member.display_name for member in guild.members},
            today,
            language=context.language,
        )

        if not entries:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description=messages.catalog.pgettext(
                    "ui", "There are no saved birthdays on this server."
                ),
                ephemeral=True,
            )
            return

        embed = create_birthday_list_embed(
            guild.name, entries, language=context.language
        )
        await FeedbackUI.send(interaction, embed=embed, ephemeral=ephemeral)


async def setup(bot: "StupidBot") -> None:
    """Register the birthday cog."""
    await bot.add_cog(BirthdayCog(bot, bot.birthday_repository))
