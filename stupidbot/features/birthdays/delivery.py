"""Reconcile birthday roles and deliver congratulations from versioned claims."""

import asyncio
import logging
import secrets
from datetime import date
from typing import Literal
from uuid import uuid4

import discord
from discord.errors import Forbidden, HTTPException
from discord.ext import commands

from stupidbot.discord_ui.embeds import SafeEmbed
from stupidbot.discord_ui.theme import BOT_ICON
from stupidbot.features.birthdays.dates import is_birthday_today
from stupidbot.features.birthdays.messages import BirthdayMessages
from stupidbot.features.birthdays.models import (
    BirthdayDelivery,
    BirthdayGuildConfig,
    BirthdayUser,
)
from stupidbot.features.birthdays.presentation import BIRTHDAY_WISHES
from stupidbot.features.birthdays.repository import BirthdayRepository
from stupidbot.i18n.catalog import catalog, pgettext_noop
from stupidbot.i18n.discord import guild_context

logger = logging.getLogger(__name__)


async def safe_fetch_member(
    guild: discord.Guild, user_id: int
) -> discord.Member | None:
    """Use the cached member or fetch it, retrying one server error.

    Return ``None`` for missing members, denied access and HTTP 400 responses.
    Other request failures propagate.
    """
    member = guild.get_member(user_id)
    if member:
        return member
    for attempt in (1, 2):
        try:
            return await guild.fetch_member(user_id)
        except (discord.NotFound, discord.Forbidden):
            return None
        except discord.HTTPException as exc:
            if exc.status >= 500 and attempt == 1:
                logger.debug("Server error fetching member %s: %s", user_id, exc)
                await asyncio.sleep(2)
                continue
            if exc.status in (400, 403, 404):
                return None
            raise
    return None


async def safe_role_edit(
    member: discord.Member,
    role: discord.Role,
    operation: Literal["add", "remove"],
) -> bool:
    """Return whether Discord accepted the role change.

    HTTP 400, 403 and 404 responses return ``False``; other failures propagate.
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


class BirthdayDeliveryService:
    """Reconcile roles and deliver versioned claims outside SQL transactions."""

    def __init__(self, bot: commands.Bot, repository: BirthdayRepository) -> None:
        self.bot = bot
        self.repository = repository
        self._unavailable_channels: dict[int, int | None] = {}

    async def process_guild(self, guild_id: int, today: date) -> None:
        """Update roles first, then send any congratulations due on ``today``."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        config = await self.repository.get(guild_id)
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
                if member is None or not await self.repository.versions_current(
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
            if not await self.repository.claim_delivery(claim):
                return
            context = await guild_context(self.bot, guild.id)
            messages = BirthdayMessages(catalog("birthdays", context.language))
            source = secrets.choice(
                BIRTHDAY_WISHES or [pgettext_noop("wish", "Happy birthday!")]
            )
            wish = messages.catalog.pgettext("wish", source)
            embed = SafeEmbed(
                title=messages.congratulations(name=user.name),
                description=f"{wish} {member.mention}",
                color=discord.Color.gold(),
            )
            embed.set_thumbnail(url=BOT_ICON)

            if not await self.repository.begin_delivery(claim):
                return
            message = await channel.send(embed=embed)
            await self.repository.finish_delivery(claim, message.id)
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
                await self.repository.release_delivery(claim)
            except Exception:
                logger.exception(
                    "Failed to release birthday claim: guild=%s user=%s operation=%s",
                    guild.id,
                    user.user_id,
                    claim.operation_id,
                )
