import logging
import random
from copy import deepcopy
from datetime import date, datetime

import discord
from discord import Button, Interaction, app_commands
from discord.ext import commands, tasks

# Global config values (assumed to be defined in your config.py)
from config import BIRTHDAY_CHECK_INTERVAL, BIRTHDAY_FILE, BIRTHDAY_WISHES, BOT_ICON

# Import JSON helpers from your utils (or directly use json_utils functions)
from utils.block_manager import BlockManager
from utils.json_utils import get_json, save_json

DATE_FORMAT = "%d-%m-%Y"  # canonical format: DD-MM-YYYY


def parse_birthday(date_str: str) -> str:
    """
    Attempt to parse a birthday string provided in DD-MM-YYYY or YYYY-MM-DD format,
    and return it as a string in DD-MM-YYYY format.
    """
    for fmt in (DATE_FORMAT, "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime(DATE_FORMAT)
        except ValueError:
            continue
    raise ValueError("Invalid date format. Use DD-MM-YYYY or YYYY-MM-DD.")


class ConfirmDeleteView(discord.ui.View):
    def __init__(self, user_id: str, server_id: str) -> None:
        super().__init__(timeout=30)
        self.user_id: str = user_id
        self.server_id: str = server_id

    @discord.ui.button(label="Да", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: Interaction, button: Button) -> None:
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message(
                "Вы не можете выполнить это действие.", ephemeral=True
            )
            return

        data = get_json(BIRTHDAY_FILE) or {}
        user_data = data.get(self.server_id, {}).get("Users", {}).get(self.user_id, {})
        if "birthday" in user_data:
            del user_data["birthday"]
            try:
                save_json(BIRTHDAY_FILE, data)
                await interaction.response.edit_message(
                    content="✅ Ваш день рождения удалён.", view=None
                )
            except Exception as e:
                logging.getLogger("BirthdayCog").error(
                    "Error saving birthday file after deletion: %s", e
                )
                await interaction.response.edit_message(
                    content="❌ Ошибка записи.", view=None
                )
        else:
            await interaction.response.edit_message(
                content="❌ У вас нет сохранённого дня рождения.", view=None
            )

    @discord.ui.button(label="Нет", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: Interaction, button: Button) -> None:
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message(
                "Вы не можете выполнить это действие.", ephemeral=True
            )
            return
        await interaction.response.edit_message(content="Отменено.", view=None)


class BirthdayCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("BirthdayCog")
        self.birthday_timer.start()

    def cog_unload(self):
        self.birthday_timer.cancel()

    async def interaction_check(self, interaction: Interaction):
        check = super().interaction_check(interaction) and interaction.guild is not None
        if interaction.guild and BlockManager.is_user_blocked(
            interaction.guild.id, interaction.user.id
        ):
            await interaction.response.send_message(
                "⛔ Доступ к командам запрещён.", ephemeral=True
            )
            self.logger.info(f"User {interaction.user} is blocked.")
            return False

        if not check:
            await interaction.response.send_message(
                "Вы должны быть на сервере.", ephemeral=True, silent=True
            )
        return check

    @tasks.loop(seconds=BIRTHDAY_CHECK_INTERVAL)
    async def birthday_timer(self):
        """Main timer loop for birthday checks."""
        try:
            data: dict = get_json(BIRTHDAY_FILE) or {}
            data_copy = deepcopy(data)
        except Exception as e:
            self.logger.error("Failed to load birthday data: %s", e)
            return

        today = date.today()
        today_key = today.strftime("%d-%m")
        today_full = today.strftime(DATE_FORMAT)

        for server_id in list(data.keys()):
            await self._process_server(
                server_id, data[server_id], today_key, today_full
            )

        if data != data_copy:
            try:
                save_json(BIRTHDAY_FILE, data)
            except Exception as e:
                self.logger.error("Error saving birthday data: %s", e)

    async def _process_server(
        self, server_id: str, server_data: dict, today_key: str, today_full: str
    ):
        """Process a single server's birthday configuration."""
        guild = self.bot.get_guild(int(server_id))
        if not guild:
            return

        channel = self.bot.get_channel(int(server_data.get("Channel_id", 0)))
        role_id = server_data.get("Birthday_role")
        role = discord.utils.get(guild.roles, id=int(role_id)) if role_id else None

        if not channel:
            self.logger.warning("Channel not configured for server %s", server_id)
            return

        # Process all users in the server
        for user_id in list(server_data.get("Users", {}).keys()):
            user_data = server_data["Users"][user_id]
            await self._process_user(
                guild, channel, role, user_id, user_data, today_key, today_full
            )

    async def _process_user(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        role: discord.Role,
        user_id: str,
        user_data: dict,
        today_key: str,
        today_full: str,
    ):
        """Process a single user's birthday status."""
        birthday = user_data.get("birthday", "")

        if not birthday:
            return

        if len(birthday) != 10:
            self.logger.warning("Invalid birthday format for user %s", user_id)
            return

        try:
            member = await guild.fetch_member(int(user_id))
        except discord.HTTPException as e:
            self.logger.error("Could not fetch member %s: %s", user_id, e)
            return

        user_bday_key = birthday[:5]  # Extract DD-MM

        if user_bday_key == today_key:
            await self._handle_birthday_case(
                member, channel, role, user_data, user_id, today_full
            )
        else:
            await self._handle_regular_case(member, role)

    async def _handle_birthday_case(
        self,
        member: discord.Member,
        channel: discord.TextChannel,
        role: discord.Role,
        user_data: dict,
        user_id: str,
        today_full: str,
    ) -> None:
        """Handle birthday congratulations and role management."""
        was_congrats = user_data.get("was_congrats", [])
        if today_full in was_congrats:
            return

        try:
            # Add birthday role if configured
            if role and role not in member.roles:
                await member.add_roles(role)
                self.logger.info(
                    "Added birthday role (%s) to member %s", role.name, user_id
                )

            # Send birthday message
            wish = random.choice(BIRTHDAY_WISHES)
            embed = discord.Embed(
                title=f"ПОЗДРАВЛЕНИЯ {user_data.get('name', 'Пользователь')}",
                description=f"{wish} {member.mention}",
                color=discord.Color.gold(),
            )
            embed.set_thumbnail(url=BOT_ICON)
            await channel.send(embed=embed)

            # Mark as congratulated only after successful operations
            user_data.setdefault("was_congrats", []).append(today_full)

        except Exception as e:
            self.logger.error("Failed to handle birthday for %s: %s", user_id, e)

    async def _handle_regular_case(self, member, role):
        """Remove birthday role if present on non-birthdays."""
        if role and role in member.roles:
            try:
                await member.remove_roles(role)
                self.logger.info("Removed birthday role from %s", member.display_name)
            except discord.HTTPException as e:
                self.logger.error("Failed to remove role from %s: %s", member.id, e)

    @birthday_timer.before_loop
    async def before_birthday_timer(self):
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="setbirthday",
        description="Установить свой день рождения (формат: ДД-ММ-ГГГГ или ГГГГ-ММ-ДД)",
    )
    @app_commands.describe(
        date_input="Дата рождения (например: 15-05-2000 или 2000-05-15)"
    )
    @app_commands.guild_only()
    async def set_birthday(self, interaction: Interaction, date_input: str):
        """
        Set your birthday.

        **Input Format:**
        Provide a date string in either DD-MM-YYYY or YYYY-MM-DD.

        **Example:**
        `/setbirthday date_input:10-09-2021`
        """
        try:
            normalized_date = parse_birthday(date_input)
        except ValueError:
            return await interaction.response.send_message(
                "Неверный формат даты. Используйте ДД-ММ-ГГГГ или ГГГГ-ММ-ДД",
                ephemeral=True,
            )

        try:
            data: dict = get_json(BIRTHDAY_FILE)
        except Exception as e:
            self.logger.error("Error loading birthday file: %s", e)
            data = {}

        author_id = str(interaction.user.id)
        guild = interaction.guild

        if guild is None:
            await interaction.response.send_message(
                "Эта команда работает только на сервере.", ephemeral=True
            )
            return
        server_id = str(guild.id)
        data.setdefault(
            server_id,
            {
                "Server_name": guild.name,
                "Channel_id": str(interaction.channel.id),
                "Users": {},
            },
        )
        data[server_id]["Users"].setdefault(
            author_id,
            {"name": interaction.user.name, "birthday": "", "was_congrats": []},
        )
        data[server_id]["Users"][author_id]["birthday"] = normalized_date
        try:
            save_json(BIRTHDAY_FILE, data)
            msg = f"Ваш день рождения записан как: {normalized_date} под именем <@{author_id}>"
            await interaction.response.send_message(msg, ephemeral=True)
        except Exception as e:
            self.logger.error("Error saving birthday file: %s", e)
            await interaction.response.send_message(
                "Ошибка записи файла.", ephemeral=True
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
        role: discord.Role = None,
    ) -> None:
        """Configure birthday system for the server"""
        server_id: str = str(interaction.guild.id)

        data: dict = get_json(BIRTHDAY_FILE) or {}
        data.setdefault(
            server_id,
            {
                "Server_name": interaction.guild.name,
                "Users": {},
            },
        )
        data[server_id].update(
            {
                "Channel_id": str(channel.id),
                "Birthday_role": str(role.id) if role else None,
            }
        )
        try:
            save_json(BIRTHDAY_FILE, data)
            response: str = f"✅ Настройки обновлены:\n- Канал: {channel.mention}"
            if role:
                response += f"\n- Роль: {role.mention}"
            await interaction.response.send_message(response, ephemeral=True)
        except Exception as e:
            self.logger.error("Error saving birthday configuration: %s", e)
            await interaction.response.send_message(
                "❌ Ошибка сохранения настроек.", ephemeral=True
            )

    @app_commands.command(
        name="remove-birthday", description="Удалить свой день рождения из системы"
    )
    @app_commands.guild_only()
    async def remove_birthday(self, interaction: Interaction):
        """Remove your birthday from the system"""
        server_id = str(interaction.guild.id)
        user_id = str(interaction.user.id)
        data = get_json(BIRTHDAY_FILE) or {}
        if server_id not in data:
            await interaction.response.send_message(
                "❌ На этом сервере нет системы дней рождений", ephemeral=True
            )
            return

        if user_id not in data[server_id].get("Users", {}):
            await interaction.response.send_message(
                "❌ У вас нет сохранённого дня рождения", ephemeral=True
            )
            return

        await interaction.response.send_message(
            "❓ Вы уверены, что хотите удалить свой день рождения?",
            view=ConfirmDeleteView(user_id, server_id),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(BirthdayCog(bot))
