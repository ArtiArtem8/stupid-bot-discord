"""Server monitoring cog for tracking and restoring member roles."""

import asyncio
import logging
from typing import TYPE_CHECKING, override

import discord
from discord import app_commands
from discord.ext import commands, tasks

from stupidbot.discord_ui.base_cog import BaseCog
from stupidbot.discord_ui.feedback import FeedbackType, FeedbackUI
from stupidbot.discord_ui.theme import Color
from stupidbot.features.member_roles.messages import RoleMessages
from stupidbot.features.member_roles.restoration import ServerMonitoringManager
from stupidbot.i18n.catalog import catalog
from stupidbot.i18n.discord import interaction_context

if TYPE_CHECKING:
    from stupidbot.app.bot import StupidBot

logger = logging.getLogger(__name__)


class ServerMonitorCog(BaseCog):
    """Monitors server members and restores roles when they rejoin."""

    def __init__(self, bot: commands.Bot, manager: ServerMonitoringManager) -> None:
        super().__init__(bot)
        self.manager = manager

    @override
    async def cog_load(self) -> None:
        """Start snapshot cleanup after the cog is registered."""
        if not self.cleanup_task.is_running():
            self.cleanup_task.start()

    @override
    async def cog_unload(self) -> None:
        """Stop background tasks on unload."""
        self.cleanup_task.cancel()
        if task := self.cleanup_task.get_task():
            await asyncio.gather(task, return_exceptions=True)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.bot:
            return

        count = await self.manager.save_snapshot(member)
        if count > 0:
            logger.info(
                "Saved %d roles for %s (ID: %d) in guild %d",
                count,
                member,
                member.id,
                member.guild.id,
            )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return

        if not await self.manager.is_enabled(member.guild.id):
            return

        restored, skipped = await self.manager.restore_snapshot(member)

        if restored:
            role_names = ", ".join(role.name for role in restored)
            logger.info(
                "Restored %d roles for %s (ID: %d) in guild %d: %s",
                len(restored),
                member,
                member.id,
                member.guild.id,
                role_names,
            )
        if skipped:
            logger.warning(
                "Role restore incomplete: guild=%s user=%s restored=%d skipped=%s",
                member.guild.id,
                member.id,
                len(restored),
                skipped,
            )

    monitor = app_commands.Group(
        name="monitor",
        description=app_commands.locale_str(
            "Manage server monitoring and role restoration",
            domain="member_roles",
        ),
        default_permissions=discord.Permissions(administrator=True),
        guild_only=True,
    )

    @monitor.command(
        name="enable",
        description=app_commands.locale_str(
            "Enable server monitoring and automatic role restoration",
            domain="member_roles",
        ),
    )
    @app_commands.describe(
        ttl_days=app_commands.locale_str(
            "Days to retain snapshots. Leave empty for unlimited retention",
            domain="member_roles",
        )
    )
    async def monitor_enable(
        self, interaction: discord.Interaction, ttl_days: int | None = None
    ) -> None:
        context = await interaction_context(interaction, private=True)
        messages = RoleMessages(catalog("member_roles", context.language))
        guild = await self._require_guild(interaction)

        if not guild.me.guild_permissions.manage_roles:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                title=messages.catalog.pgettext("ui", "Insufficient permissions"),
                description=messages.catalog.pgettext(
                    "ui", "The bot needs the Manage Roles permission."
                ),
                ephemeral=True,
            )
            return

        if ttl_days is not None and ttl_days < 1:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description=messages.catalog.pgettext(
                    "ui", "TTL must be positive or left empty (unlimited)."
                ),
                ephemeral=True,
            )
            return

        await self.manager.set_enabled(guild.id, True, ttl_days)
        logger.info("Monitoring enabled for guild %d with TTL=%s", guild.id, ttl_days)

        ttl_text = (
            messages.catalog.pgettext("ui", "unlimited retention")
            if ttl_days is None
            else messages.ttl(days=ttl_days)
        )
        msg = messages.enabled(retention=ttl_text)

        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.SUCCESS,
            title=messages.catalog.pgettext("ui", "Monitoring enabled"),
            description=msg,
            ephemeral=True,
        )

    @monitor.command(
        name="disable",
        description=app_commands.locale_str(
            "Disable server monitoring", domain="member_roles"
        ),
    )
    async def monitor_disable(self, interaction: discord.Interaction) -> None:
        context = await interaction_context(interaction, private=True)
        messages = RoleMessages(catalog("member_roles", context.language))
        guild = await self._require_guild(interaction)

        if not await self.manager.is_enabled(guild.id):
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.INFO,
                description=messages.catalog.pgettext(
                    "ui", "Monitoring is already disabled on this server."
                ),
                ephemeral=True,
            )
            return

        await self.manager.set_enabled(guild.id, False)
        logger.info("Monitoring disabled for guild %d", guild.id)
        msg = messages.catalog.pgettext(
            "ui",
            "Saved role snapshots have been kept. Use `/monitor forget` to delete "
            "them.",
        )
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.SUCCESS,
            title=messages.catalog.pgettext("ui", "Monitoring disabled"),
            description=msg,
            ephemeral=True,
        )

    @monitor.command(
        name="status",
        description=app_commands.locale_str(
            "Show monitoring status and saved members",
            domain="member_roles",
        ),
    )
    async def monitor_status(self, interaction: discord.Interaction) -> None:
        context = await interaction_context(interaction, private=True)
        messages = RoleMessages(catalog("member_roles", context.language))
        guild = await self._require_guild(interaction)

        enabled = await self.manager.is_enabled(guild.id)
        ttl = await self.manager.get_ttl(guild.id)
        snapshots = await self.manager.get_all_snapshots(guild.id)

        embed = discord.Embed(
            title=messages.status(guild=guild.name),
            color=Color.INFO,
        )

        status_emoji = "✅" if enabled else "❌"
        status_text = (
            messages.catalog.pgettext("ui", "Enabled")
            if enabled
            else messages.catalog.pgettext("ui", "Disabled")
        )
        ttl_text = (
            messages.catalog.pgettext("ui", "unlimited")
            if ttl is None
            else messages.days(days=ttl)
        )

        embed.add_field(
            name=messages.catalog.pgettext("ui", "Status"),
            value=f"{status_emoji} {status_text}",
            inline=True,
        )
        embed.add_field(name="TTL", value=ttl_text, inline=True)
        embed.add_field(
            name=messages.catalog.pgettext("ui", "Snapshots saved"),
            value=str(len(snapshots)),
            inline=True,
        )

        if snapshots:
            snapshot_lines: list[str] = []
            for snapshot in snapshots[:10]:
                timestamp = discord.utils.format_dt(snapshot.left_at, style="R")
                msg = messages.snapshot(
                    name=snapshot.username,
                    count=len(snapshot.roles),
                    timestamp=timestamp,
                )
                snapshot_lines.append(msg)

            embed.add_field(
                name=messages.recent(shown=len(snapshots[:10]), total=len(snapshots)),
                value="\n".join(snapshot_lines),
                inline=False,
            )

            if len(snapshots) > 10:
                embed.set_footer(text=messages.footer(total=len(snapshots)))

        await FeedbackUI.send(interaction, embed=embed, ephemeral=True)

    @monitor.command(
        name="forget",
        description=app_commands.locale_str(
            "Delete a member's saved role snapshot", domain="member_roles"
        ),
    )
    @app_commands.describe(
        user=app_commands.locale_str(
            "Member whose snapshot should be deleted", domain="member_roles"
        )
    )
    async def monitor_forget(
        self, interaction: discord.Interaction, user: discord.User
    ) -> None:
        context = await interaction_context(interaction, private=True)
        messages = RoleMessages(catalog("member_roles", context.language))
        guild = await self._require_guild(interaction)

        deleted = await self.manager.delete_snapshot(guild.id, user.id)

        if deleted:
            logger.info(
                "Deleted snapshot for user %d in guild %d by admin %d",
                user.id,
                guild.id,
                interaction.user.id,
            )
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.SUCCESS,
                description=messages.deleted(mention=user.mention),
                ephemeral=True,
            )
        else:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.INFO,
                description=messages.missing(mention=user.mention),
                ephemeral=True,
            )

    @monitor.command(
        name="restore",
        description=app_commands.locale_str(
            "Manually restore a member's roles", domain="member_roles"
        ),
    )
    @app_commands.describe(
        user=app_commands.locale_str(
            "Member whose roles should be restored", domain="member_roles"
        )
    )
    async def monitor_restore(
        self, interaction: discord.Interaction, user: discord.Member
    ) -> None:
        context = await interaction_context(interaction, private=True)
        messages = RoleMessages(catalog("member_roles", context.language))
        guild = await self._require_guild(interaction)

        snapshot = await self.manager.get_snapshot(guild.id, user.id)
        if not snapshot:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.INFO,
                description=messages.missing(mention=user.mention),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        restored, skipped = await self.manager.restore_snapshot(user)

        if not restored and not skipped:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.INFO,
                description=(
                    messages.catalog.pgettext(
                        "ui",
                        "No roles to restore: the snapshot is missing or expired, or "
                        "the roles have already been granted.",
                    )
                ),
                ephemeral=True,
            )
            return

        role_names = ", ".join(f"`{role.name}`" for role in restored)
        description = messages.restored(count=len(restored))

        if restored:
            description += f"\n\n{role_names}"

        if skipped:
            description += messages.skipped(count=len(skipped))

        logger.info(
            "Restore for user %d in guild %d by admin %d: %d restored, %d skipped",
            user.id,
            guild.id,
            interaction.user.id,
            len(restored),
            len(skipped),
        )

        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.SUCCESS if restored else FeedbackType.WARNING,
            title=messages.restoration(name=user.display_name),
            description=description,
            ephemeral=True,
        )

    @tasks.loop(hours=24)
    async def cleanup_task(self) -> None:
        logger.debug("Running cleanup task for expired snapshots")

        for guild in self.bot.guilds:
            try:
                removed = await self.manager.cleanup_expired(guild.id)
                if removed > 0:
                    logger.info(
                        "Cleaned up %d expired snapshots in guild %d", removed, guild.id
                    )
            except Exception:
                logger.exception("Error cleaning up guild %d", guild.id)

    @cleanup_task.before_loop
    async def before_cleanup_task(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: "StupidBot") -> None:
    """Register the guild-monitoring cog."""
    await bot.add_cog(ServerMonitorCog(bot, bot.monitor_manager))
