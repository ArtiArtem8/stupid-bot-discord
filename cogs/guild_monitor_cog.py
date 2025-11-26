"""Server monitoring cog for tracking and restoring member roles."""

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from api.guild_monitoring import monitor_manager
from framework.base_cog import BaseCog
from framework.feedback_ui import FeedbackType, FeedbackUI


class ServerMonitorCog(BaseCog):
    """Monitors server members and restores roles when they rejoin."""

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(bot)
        self.cleanup_task.start()

    async def cog_unload(self) -> None:
        """Stop background tasks on unload."""
        self.cleanup_task.cancel()

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Save role snapshot when a member leaves."""
        if member.bot:
            return

        count = monitor_manager.save_snapshot(member)
        if count > 0:
            self.logger.info(
                "Saved %d roles for %s (ID: %d) in guild %d",
                count,
                member,
                member.id,
                member.guild.id,
            )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Restore roles when a member rejoins."""
        if member.bot:
            return

        if not monitor_manager.is_enabled(member.guild.id):
            return

        restored, skipped = await monitor_manager.restore_snapshot(member)

        if restored:
            role_names = ", ".join(role.name for role in restored)
            self.logger.info(
                "Restored %d roles for %s (ID: %d) in guild %d: %s",
                len(restored),
                member,
                member.id,
                member.guild.id,
                role_names,
            )
            if skipped:
                self.logger.warning(
                    "Skipped %d roles for %s (deleted or unpermitted): %s",
                    len(skipped),
                    member,
                    skipped,
                )

    monitor = app_commands.Group(
        name="monitor",
        description="Управление мониторингом сервера и восстановлением ролей",
        default_permissions=discord.Permissions(administrator=True),
        guild_only=True,
    )

    @monitor.command(
        name="enable",
        description="Включить мониторинг сервера и автовосстановление ролей",
    )
    @app_commands.describe(
        ttl_days="Количество дней хранения снимков. Пусто = бесконечно"
    )
    async def monitor_enable(
        self, interaction: discord.Interaction, ttl_days: int | None = None
    ) -> None:
        """Enable server monitoring."""
        guild = await self._require_guild(interaction)

        # Validate permissions
        if not guild.me.guild_permissions.manage_roles:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                title="Недостаточно прав",
                description="Боту необходимо разрешение управление ролями.",
                ephemeral=True,
            )
            return

        if ttl_days is not None and ttl_days < 1:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description="TTL должен быть положительным или не указан (бесконечно).",
                ephemeral=True,
            )
            return

        monitor_manager.set_enabled(guild.id, True, ttl_days)
        self.logger.info(
            "Monitoring enabled for guild %d with TTL=%s", guild.id, ttl_days
        )

        ttl_text = (
            "бесконечное хранение" if ttl_days is None else f"TTL: {ttl_days} дней"
        )
        msg = (
            "Бот будет восстанавливать роли участникам при повторном входе "
            f"({ttl_text})."
        )

        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.SUCCESS,
            title="Мониторинг включён",
            description=msg,
            ephemeral=True,
        )

    @monitor.command(name="disable", description="Отключить мониторинг сервера")
    async def monitor_disable(self, interaction: discord.Interaction) -> None:
        """Disable server monitoring."""
        guild = await self._require_guild(interaction)

        if not monitor_manager.is_enabled(guild.id):
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.INFO,
                description="Мониторинг уже отключён на этом сервере.",
                ephemeral=True,
            )
            return

        monitor_manager.set_enabled(guild.id, False)
        self.logger.info("Monitoring disabled for guild %d", guild.id)
        msg = (
            "Сохранённые снимки ролей не удалены. "
            "Используйте `/monitor forget` для очистки."
        )
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.SUCCESS,
            title="Мониторинг отключён",
            description=msg,
            ephemeral=True,
        )

    @monitor.command(
        name="status",
        description="Показать статус мониторинга и список сохранённых участников",
    )
    async def monitor_status(self, interaction: discord.Interaction) -> None:
        """Show monitoring status and snapshot list."""
        guild = await self._require_guild(interaction)

        enabled = monitor_manager.is_enabled(guild.id)
        ttl = monitor_manager.get_ttl(guild.id)
        snapshots = monitor_manager.get_all_snapshots(guild.id)

        embed = discord.Embed(
            title=f"Статус мониторинга: {guild.name}",
            color=config.Color.INFO,
        )

        status_emoji = "✅" if enabled else "❌"
        status_text = "Включён" if enabled else "Отключён"
        ttl_text = "бесконечно" if ttl is None else f"{ttl} дней"

        embed.add_field(
            name="Статус", value=f"{status_emoji} {status_text}", inline=True
        )
        embed.add_field(name="TTL", value=ttl_text, inline=True)
        embed.add_field(
            name="Снимков сохранено", value=str(len(snapshots)), inline=True
        )

        if snapshots:
            snapshot_lines: list[str] = []
            for snapshot in snapshots[:10]:
                timestamp = discord.utils.format_dt(snapshot.left_at, style="R")
                msg = (
                    f"• **{snapshot.username}** — {len(snapshot.roles)} ролей, "
                    f"вышел {timestamp}"
                )
                snapshot_lines.append(msg)

            embed.add_field(
                name=f"Недавние участники ({len(snapshots[:10])} из {len(snapshots)})",
                value="\n".join(snapshot_lines),
                inline=False,
            )

            if len(snapshots) > 10:
                embed.set_footer(text=f"Показаны первые 10 из {len(snapshots)} снимков")

        await FeedbackUI.send(interaction, embed=embed, ephemeral=True)

    @monitor.command(
        name="forget", description="Удалить сохранённый снимок ролей участника"
    )
    @app_commands.describe(user="Участник, чей снимок нужно удалить")
    async def monitor_forget(
        self, interaction: discord.Interaction, user: discord.User
    ) -> None:
        """Forget a member's role snapshot."""
        guild = await self._require_guild(interaction)

        deleted = monitor_manager.delete_snapshot(guild.id, user.id)

        if deleted:
            self.logger.info(
                "Deleted snapshot for user %d in guild %d by admin %d",
                user.id,
                guild.id,
                interaction.user.id,
            )
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.SUCCESS,
                description=f"Снимок ролей для {user.mention} удалён.",
                ephemeral=True,
            )
        else:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.INFO,
                description=f"Для {user.mention} не найдено сохранённых ролей.",
                ephemeral=True,
            )

    @monitor.command(name="restore", description="Вручную восстановить роли участнику")
    @app_commands.describe(user="Участник, которому нужно восстановить роли")
    async def monitor_restore(
        self, interaction: discord.Interaction, user: discord.Member
    ) -> None:
        """Manually restore a member's roles."""
        guild = await self._require_guild(interaction)

        snapshot = monitor_manager.get_snapshot(guild.id, user.id)
        if not snapshot:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.INFO,
                description=f"Для {user.mention} не найдено сохранённых ролей.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        restored, skipped = await monitor_manager.restore_snapshot(user)

        if not restored and not skipped:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                title="Ошибка восстановления",
                description="Не удалось восстановить ни одной роли. Проверьте права.",
                ephemeral=True,
            )
            return

        role_names = ", ".join(f"`{role.name}`" for role in restored)
        description = f"Восстановлено ролей: {len(restored)}"

        if restored:
            description += f"\n\n{role_names}"

        if skipped:
            description += (
                f"\n\n⚠️ Пропущено {len(skipped)} ролей (удалены или недостаточно прав)"
            )

        self.logger.info(
            "Restore for user %d in guild %d by admin %d: %d restored, %d skipped",
            user.id,
            guild.id,
            interaction.user.id,
            len(restored),
            len(skipped),
        )

        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.SUCCESS,
            title=f"Роли восстановлены для {user.display_name}",
            description=description,
            ephemeral=True,
        )

    @tasks.loop(hours=24)
    async def cleanup_task(self) -> None:
        """Background task to clean up expired snapshots."""
        self.logger.debug("Running cleanup task for expired snapshots")

        for guild in self.bot.guilds:
            try:
                removed = monitor_manager.cleanup_expired(guild.id)
                if removed > 0:
                    self.logger.info(
                        "Cleaned up %d expired snapshots in guild %d", removed, guild.id
                    )
            except Exception as e:
                self.logger.error(
                    "Error cleaning up guild %d: %s", guild.id, e, exc_info=True
                )


async def setup(bot: commands.Bot) -> None:
    """Load the cog."""
    await bot.add_cog(ServerMonitorCog(bot))
