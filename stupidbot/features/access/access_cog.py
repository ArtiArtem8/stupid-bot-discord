"""Administrative commands for user blocking and management.

Provides:
- Blocking/unblocking users from bot access
- Viewing detailed block history
- Listing all blocked users
- Tracking name changes over time

"""

import logging
from typing import TYPE_CHECKING, override

import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import format_dt

from stupidbot.discord_ui.base_cog import BaseCog
from stupidbot.discord_ui.embeds import SafeEmbed
from stupidbot.discord_ui.feedback import FeedbackType, FeedbackUI
from stupidbot.discord_ui.pagination import BasePaginator
from stupidbot.discord_ui.theme import Color
from stupidbot.features.access.operations import BlockManager

if TYPE_CHECKING:
    from stupidbot.app.bot import StupidBot


from stupidbot.features.access.messages import AccessMessages
from stupidbot.features.access.presentation import (
    BLOCK,
    UNBLOCK,
    BlockedListPages,
    create_block_embed,
    format_blocked_user,
    format_danger_level,
    format_name_history,
    format_recent_block_events,
)
from stupidbot.i18n.catalog import catalog
from stupidbot.i18n.discord import interaction_context

logger = logging.getLogger(__name__)


class AdminCog(BaseCog):
    """Administrative commands for server management.

    Default command permissions target administrators; owner checks stay explicit.
    """

    def __init__(self, bot: commands.Bot, block_manager: BlockManager) -> None:
        super().__init__(bot)
        self.block_manager = block_manager

    @override
    def should_bypass_block(self, interaction: discord.Interaction) -> bool:
        """Allow admin commands to bypass block checks."""
        return True

    @app_commands.command(
        name="block",
        description=app_commands.locale_str(
            "Заблокировать пользователя от использования бота.", domain="access"
        ),
    )
    @app_commands.describe(
        user=app_commands.locale_str(
            "Пользователь, которого надо лишить доступа к этому боту", domain="access"
        ),
        reason=app_commands.locale_str("Причина блокировки", domain="access"),
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def block(
        self, interaction: discord.Interaction, user: discord.Member, reason: str = ""
    ) -> None:
        context = await interaction_context(interaction, private=True)
        messages = AccessMessages(catalog("access", context.language))
        guild = await self._require_guild(interaction)
        if await self.block_manager.is_user_blocked(guild.id, user.id):
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description=messages.already_blocked(mention=user.mention),
                ephemeral=True,
            )
            return
        await self.block_manager.block_user(guild.id, user, interaction.user.id, reason)
        logger.info("Blocked user %d in guild %d", user.id, guild.id)
        embed = create_block_embed(user, BLOCK, reason, language=context.language)
        await FeedbackUI.send(interaction, embed=embed, ephemeral=True)

    @app_commands.command(
        name="unblock",
        description=app_commands.locale_str(
            "Снять блокировку использования бота с пользователя.", domain="access"
        ),
    )
    @app_commands.describe(
        user=app_commands.locale_str(
            "Пользователь, с которого снимается блокировка", domain="access"
        ),
        reason=app_commands.locale_str("Причина снятия блокировки", domain="access"),
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def unblock(
        self, interaction: discord.Interaction, user: discord.Member, reason: str = ""
    ) -> None:
        context = await interaction_context(interaction, private=True)
        messages = AccessMessages(catalog("access", context.language))
        guild = await self._require_guild(interaction)
        if not await self.block_manager.is_user_blocked(guild.id, user.id):
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description=messages.not_blocked(mention=user.mention),
                ephemeral=True,
            )
            return
        await self.block_manager.unblock_user(
            guild.id, user, interaction.user.id, reason
        )
        logger.info("Unblocked user %d in guild %d", user.id, guild.id)
        embed = create_block_embed(user, UNBLOCK, reason, language=context.language)
        await FeedbackUI.send(interaction, embed=embed, ephemeral=True)

    @app_commands.command(
        name="block-info",
        description=app_commands.locale_str(
            "Показать подробную информацию о блокировках пользователя.", domain="access"
        ),
    )
    @app_commands.describe(
        user=app_commands.locale_str(
            "Пользователь для просмотра информации", domain="access"
        ),
        ephemeral=app_commands.locale_str(
            "Скрыть сообщение от других пользователей", domain="access"
        ),
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def blockinfo(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        ephemeral: bool = True,
    ) -> None:
        context = await interaction_context(interaction, private=ephemeral)
        messages = AccessMessages(catalog("access", context.language))
        guild = await self._require_guild(interaction)
        user_entry = await self.block_manager.get_user(guild.id, user.id)

        if not user_entry or not user_entry.block_history:
            logger.info(
                "No block history found for user %s in guild %s (%s)",
                user.id,
                guild.name,
                guild.id,
            )
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.INFO,
                description=messages.no_history(mention=user.mention),
                ephemeral=ephemeral,
            )
            return

        logger.info(
            "Displaying block history for user %s in guild %s (%s)",
            user.id,
            guild.name,
            guild.id,
        )
        embed = SafeEmbed(
            title=messages.catalog.pgettext("ui", "Полная история блокировок"),
            color=Color.ERROR if user_entry.is_blocked else Color.SUCCESS,
        )
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)
        embed.set_thumbnail(url=user.display_avatar.url)

        if user_entry.is_blocked:
            last_block = user_entry.block_history[-1]
            timestamp = format_dt(last_block.timestamp, "F")
            status_value = messages.blocked_status(
                admin_id=last_block.admin_id,
                timestamp=timestamp,
                reason=last_block.reason
                or messages.catalog.pgettext("ui", "Не указана"),
            )
        else:
            status_value = messages.catalog.pgettext("ui", "Не заблокирован")

        embed.safe_add_field(
            name=messages.catalog.pgettext("ui", "Текущий статус"),
            value=status_value,
            inline=False,
        )

        embed.safe_add_field(
            name=messages.catalog.pgettext("ui", "Последние события"),
            value=format_recent_block_events(user_entry, language=context.language),
            inline=False,
        )
        if user_entry.name_history:
            embed.safe_add_field(
                name=messages.catalog.pgettext("ui", "История имён"),
                value=format_name_history(
                    user_entry.name_history, language=context.language
                ),
            )

        first_block_ts = format_dt(user_entry.block_history[0].timestamp, "D")
        stats = [
            messages.blocks(count=len(user_entry.block_history)),
            messages.unblocks(count=len(user_entry.unblock_history)),
            messages.first_block(timestamp=first_block_ts),
        ]

        if user_entry.unblock_history:
            last_unblock_ts = format_dt(user_entry.unblock_history[-1].timestamp, "D")
            stats.append(messages.last_unblock(timestamp=last_unblock_ts))

        embed.safe_add_field(
            name=messages.catalog.pgettext("ui", "Статистика"),
            value="\n".join(stats),
            inline=False,
        )

        danger_level = format_danger_level(
            len(user_entry.block_history), language=context.language
        )
        embed.set_footer(text=messages.danger(level=danger_level))

        await FeedbackUI.send(interaction, embed=embed, ephemeral=ephemeral)

        logger.info("Displayed blockinfo for user %s in guild %s", user.id, guild.id)

    @app_commands.command(
        name="list-blocked",
        description=app_commands.locale_str(
            "Показать всех заблокированных пользователей", domain="access"
        ),
    )
    @app_commands.describe(
        show_details=app_commands.locale_str(
            "Показать дополнительную информацию", domain="access"
        ),
        ephemeral=app_commands.locale_str(
            "Скрыть сообщение от других пользователей", domain="access"
        ),
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def listblocked(
        self,
        interaction: discord.Interaction,
        show_details: bool = False,
        ephemeral: bool = True,
    ) -> None:
        context = await interaction_context(interaction, private=ephemeral)
        messages = AccessMessages(catalog("access", context.language))
        guild = await self._require_guild(interaction)
        all_users = await self.block_manager.get_guild_users(guild.id)
        blocked_users = [u for u in all_users if u.is_blocked]

        if not blocked_users:
            logger.info("No blocked users found in guild %s", guild.id)
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.INFO,
                description=messages.catalog.pgettext(
                    "ui", "Нет заблокированных пользователей."
                ),
                ephemeral=ephemeral,
            )
            return
        logger.info("Found %s blocked users in guild %s", len(blocked_users), guild.id)

        entries = [
            format_blocked_user(
                user_entry,
                guild.get_member(user_entry.user_id),
                show_details=show_details,
                language=context.language,
            )
            for user_entry in blocked_users
        ]
        pages = BlockedListPages(
            entries, show_details=show_details, language=context.language
        )
        view = BasePaginator(pages, interaction.user.id)
        await view.prepare()
        await view.send(interaction, ephemeral=ephemeral)


async def setup(bot: "StupidBot") -> None:
    """Register the administration cog."""
    await bot.add_cog(AdminCog(bot, bot.block_manager))
