import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils import BlockedUser, BlockManager


class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.block_manager = BlockManager()
        self.logger = logging.getLogger("AdminCog")

    async def _get_or_create_user_entry(
        self, guild_id: int, member: discord.Member
    ) -> tuple[BlockedUser, dict[int, BlockedUser]]:
        guild_data = self.block_manager.get_guild_data(guild_id)
        user_id = member.id

        if user_id in guild_data:
            user_entry = guild_data[user_id]
            if user_entry.update_name_history(member.display_name, member.name):
                self.logger.info(
                    f"Updated name history for user {user_id} in guild {guild_id}. "
                    f"New name: {member.display_name}, global: {member.name}"
                )
        else:
            user_entry = BlockedUser(
                user_id=user_id,
                current_username=member.display_name,
                current_global_name=member.name,
            )
            user_entry.update_name_history(member.display_name, member.name)
            guild_data[user_id] = user_entry
            self.logger.info(
                f"Created new block entry for user {user_id} in guild {guild_id}. "
                f"Initial name: {member.display_name}"
            )

        return user_entry, guild_data

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
        self.logger.info(
            f"Block command invoked by {interaction.user.id} in guild {interaction.guild.name} ({interaction.guild.id}) "
            f"targeting user {user.id}. Reason: {reason}"
        )
        user_entry, guild_data = await self._get_or_create_user_entry(
            interaction.guild.id, user
        )

        if user_entry.is_blocked:
            self.logger.info(
                f"Block attempt failed - user {user.id} already blocked in guild {interaction.guild.name} ({interaction.guild.id})"
            )
            return await interaction.response.send_message(
                f"{user.mention} уже заблокирован.", ephemeral=True
            )

        user_entry.add_block_entry(interaction.user.id, reason)
        self.block_manager.save_guild_data(interaction.guild, guild_data)

        embed = discord.Embed(
            title="Блокировка",
            color=0xFFAE00,
            description=f"{user.mention} был заблокирован.",
        )
        if reason:
            embed.add_field(name="Причина", value=reason)
        self.logger.info(
            f"Successfully blocked user {user.id} in guild {interaction.guild.name} ({interaction.guild.id})"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

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
        self.logger.info(
            f"Unblock command invoked by {interaction.user.id} in guild {interaction.guild.name} ({interaction.guild.id}) "
            f"targeting user {user.id}. Reason: {reason}"
        )
        user_entry, guild_data = await self._get_or_create_user_entry(
            interaction.guild.id, user
        )

        if not user_entry.is_blocked:
            self.logger.info(
                f"Unblock attempt failed - user {user.id} not blocked in guild {interaction.guild.name} ({interaction.guild.id})"
            )
            return await interaction.response.send_message(
                f"{user.mention} не заблокирован.", ephemeral=True
            )

        user_entry.add_unblock_entry(interaction.user.id, reason)
        self.block_manager.save_guild_data(interaction.guild, guild_data)
        embed = discord.Embed(
            title="Разблокировка",
            color=0xFFAE00,
            description=f"{user.mention} был разблокирован.",
        )
        if reason:
            embed.add_field(name="Причина", value=reason)
        self.logger.info(
            f"Successfully unblocked user {user.id} in guild {interaction.guild.name} ({interaction.guild.id})"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

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
        self.logger.info(
            f"Blockinfo requested by {interaction.user.id} for user {user.id} in guild {interaction.guild.name} ({interaction.guild.id})"
        )
        guild_data = self.block_manager.get_guild_data(interaction.guild.id)
        user_entry = guild_data.get(user.id)

        if not user_entry or not user_entry.block_history:
            self.logger.info(
                f"No block history found for user {user.id} in guild {interaction.guild.name} ({interaction.guild.id})"
            )
            await interaction.response.send_message(
                f"{user.mention} не имеет истории блокировок.", ephemeral=ephemeral
            )
            return

        self.logger.info(
            f"Displaying block history for user {user.id} in guild {interaction.guild.name} ({interaction.guild.id})"
        )
        embed = discord.Embed(title="📜 Полная история блокировок", color=0x2B2D31)
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)
        embed.set_thumbnail(url=user.display_avatar.url)

        # Current Status
        if user_entry.is_blocked:
            last_block = user_entry.block_history[-1]
            status_value = (
                f"🔴 **Заблокирован**\n"
                f"• Администратор: <@{last_block.admin_id}>\n"
                f"• Причина: {last_block.reason or 'Не указана'}\n"
                f"• Дата: <t:{int(last_block.timestamp.timestamp())}:F>"
            )
        else:
            status_value = "🟢 Не заблокирован"

        embed.add_field(name="Текущий статус", value=status_value, inline=False)

        history = []
        all_events = sorted(
            [(e.timestamp, "BLOCK", e) for e in user_entry.block_history]
            + [(e.timestamp, "UNBLOCK", e) for e in user_entry.unblock_history],
            key=lambda x: x[0],
            reverse=True,
        )[:5]

        for timestamp, action, entry in all_events:
            icon = ["🔓", "🔒"][action == "BLOCK"]
            history.append(
                f"{icon} **{action}** <t:{int(timestamp.timestamp())}:R>\n"
                f"• Админ: <@{entry.admin_id}>\n"
                f"• Причина: {entry.reason or 'Не указана'}\n"
            )

        if history:
            embed.add_field(
                name="Последние события",
                value="\n".join(history)[:1024],
                inline=False,
            )

        # Name History
        if user_entry.name_history:
            name_changes = []
            for name_entry in sorted(
                user_entry.name_history, key=lambda x: x.timestamp, reverse=True
            )[:3]:
                name_changes.append(
                    f"<t:{int(name_entry.timestamp.timestamp())}:D>:\n"
                    f"• Имя: {name_entry.username}\n"
                )

            embed.add_field(
                name="📝 История имён",
                value="\n".join(name_changes)[:1024],
            )

        stats = [
            f"• Всего блокировок: {len(user_entry.block_history)}",
            f"• Всего разблокировок: {len(user_entry.unblock_history)}",
            f"• Первая блокировка: <t:{int(user_entry.block_history[0].timestamp.timestamp())}:D>",
        ]

        if user_entry.unblock_history:
            stats.append(
                f"• Последняя разблокировка: <t:{int(user_entry.unblock_history[-1].timestamp.timestamp())}:D>"
            )

        embed.add_field(name="📊 Статистика", value="\n".join(stats), inline=False)

        notes = []
        if user_entry.block_history:
            first_block = user_entry.block_history[0]
            notes.append(
                f"Первая блокировка: <t:{int(first_block.timestamp.timestamp())}:D> "
                f"(<@{first_block.admin_id}>)"
            )

        danger_level = "🟢 Низкий"
        if len(user_entry.block_history) > 2:
            danger_level = "🟠 Средний"
        if len(user_entry.block_history) > 4:
            danger_level = "🔴 Высокий"

        embed.set_footer(text=f"Уровень проблемности: {danger_level}")
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @app_commands.command(
        name="listblocked", description="Показать всех заблокированных пользователей"
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
        """Display all currently blocked users with basic information"""
        self.logger.info(
            f"Listblocked command invoked by {interaction.user.id} in guild {interaction.guild.name} ({interaction.guild.id}) "
            f"with details: {show_details}"
        )
        blocked_users = self.block_manager.get_guild_data(interaction.guild.id)
        blocked_users = [user for user in blocked_users.values() if user.is_blocked]

        if not blocked_users:
            self.logger.info(f"No blocked users found in guild {interaction.guild.id}")
            await interaction.response.send_message(
                "🚫 Нет заблокированных пользователей на этом сервере.",
                ephemeral=ephemeral,
            )
            return
        self.logger.info(
            f"Found {len(blocked_users)} blocked users in guild {interaction.guild.id} "
        )
        embed = discord.Embed(
            title=f"Заблокированные пользователи ({len(blocked_users)})", color=0x36393F
        )

        unresolved_count = 0
        entries = []

        for user_entry in blocked_users:
            try:
                user = await interaction.guild.fetch_member(user_entry.user_id)
                user_info = f"{user.mention} `{user.id}`"
                current_username = user.display_name
            except discord.NotFound:
                user_info = f"🚷 Пользователь покинул сервер `{user_entry.user_id}`"
                current_username = user_entry.current_username
                unresolved_count += 1

            entry = [f"**Пользователь:** {user_info}"]

            if show_details:
                last_block = user_entry.block_history[-1]
                entry.extend(
                    [
                        f"• Текущее имя: {current_username}",
                        f"• Последняя блокировка: <t:{int(last_block.timestamp.timestamp())}:R>",
                        f"• Причина: {last_block.reason or 'Не указана'}",
                        f"• Администратор: <@{last_block.admin_id}>",
                    ]
                )

            entries.append("\n".join(entry))

        embed.description = (
            f"**Статистика блокировок:**\n"
            f"• Всего заблокировано: {len(blocked_users)}\n"
            f"• Не на сервере: {unresolved_count}\n"
            f"• Последняя блокировка: <t:{int(blocked_users[0].block_history[-1].timestamp.timestamp())}:R>"
        )

        MAX_FIELD_CHARS = 1000
        current_field = []
        current_length = 0

        for entry in entries:
            entry_length = len(entry)
            if current_length + entry_length > MAX_FIELD_CHARS:
                embed.add_field(
                    name="Заблокированные пользователи",
                    value="\n\n".join(current_field),
                    inline=False,
                )
                current_field = []
                current_length = 0
            current_field.append(entry)
            current_length += entry_length

        if current_field:
            embed.add_field(
                name="Заблокированные пользователи"
                if not show_details
                else "Детали блокировок",
                value="\n\n".join(current_field),
                inline=False,
            )

        embed.set_footer(
            text="" if not show_details else "Детальная информация о блокировках"
        )

        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)


async def setup(bot):
    await bot.add_cog(AdminCog(bot))
