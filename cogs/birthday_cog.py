import asyncio
import logging
import random
from copy import deepcopy
from datetime import date, datetime
from typing import Any, Literal

import discord
from discord import Button, Interaction, app_commands
from discord.errors import Forbidden, HTTPException, NotFound
from discord.ext import commands, tasks

from config import BIRTHDAY_CHECK_INTERVAL, BIRTHDAY_FILE, BIRTHDAY_WISHES, BOT_ICON
from utils.block_manager import BlockManager
from utils.json_utils import get_json, save_json

DATE_FORMAT = "%d-%m-%Y"  # canonical format: DD-MM-YYYY


def parse_birthday(date_str: str) -> str:
    """Attempt to parse a birthday string provided in DD-MM-YYYY or YYYY-MM-DD format,
    and return it as a string in DD-MM-YYYY format.
    """
    for fmt in (DATE_FORMAT, "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime(DATE_FORMAT)
        except ValueError:
            continue
    raise ValueError("Invalid date format. Use DD-MM-YYYY or YYYY-MM-DD.")


async def safe_fetch_member(
    guild: discord.Guild, user_id: int, logger: logging.Logger
) -> discord.Member | None:
    """Fetch member with retry on errors."""
    member = guild.get_member(user_id)
    if member:
        return member
    for attempt in (1, 2):
        try:
            return await guild.fetch_member(user_id)
        except (NotFound, Forbidden):
            return None
        except HTTPException as e:
            if e.status >= 500 and attempt == 1:
                logger.debug("Server error fetching member: %s", e)
                await asyncio.sleep(2)
                continue
            logger.exception("Permanent error fetching member: %s", e)
            if e.status in (400, 403, 404):
                return None
            raise


async def safe_role_edit(
    member: discord.Member,
    role: discord.Role,
    operation: Literal["add", "remove"],
    logger: logging.Logger,
) -> bool:
    """Safely add/remove roles with permission checks."""
    if not member.guild.me.guild_permissions.manage_roles:
        logger.warning("Bot lacks manage_roles permission")
        return False  # Bot lacks permission:cite[1]:cite[3]

    if role >= member.guild.me.top_role:
        logger.debug("Role too high, %s >= %s", role, member.guild.me.top_role)
        return False  # Role too high:cite[1]

    if not role.is_assignable():
        logger.debug("Role is not assignable, %s", role)
        return False

    try:
        match operation:
            case "add":
                await member.add_roles(role)
            case "remove":
                await member.remove_roles(role)
            case _:
                raise ValueError(f"Invalid operation: {operation}")  # !unreachable
        return True
    except Forbidden:
        return False  # Missing permissions:cite[3]
    except HTTPException as exc:
        if exc.status in (400, 403, 404):
            return False
        raise


class ConfirmDeleteView(discord.ui.View):
    def __init__(self, user_id: str, server_id: str) -> None:
        super().__init__(timeout=30)
        self.user_id: str = user_id
        self.server_id: str = server_id

    @discord.ui.button(label="Да", style=discord.ButtonStyle.green)  # type: ignore
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

    @discord.ui.button(label="Нет", style=discord.ButtonStyle.red)  # type: ignore
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

    async def cog_unload(self):
        self.birthday_timer.cancel()

    async def interaction_check(self, interaction: Interaction):  # type: ignore
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
            raw = get_json(BIRTHDAY_FILE)
            if not isinstance(raw, dict):
                self.logger.error("Top-level JSON is not an object")
                return
            data = deepcopy(raw)
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

        if data != raw:
            try:
                self.logger.debug("Saving updated birthday data")
                save_json(BIRTHDAY_FILE, data)
            except Exception as e:
                self.logger.error("Error saving birthday data: %s", e)

    async def _process_server(
        self,
        server_id: str,
        server_data: dict[str, Any],
        today_key: str,
        today_full: str,
    ):
        """Process a single server's birthday configuration."""
        guild = self.bot.get_guild(int(server_id))
        if not guild:
            self.logger.debug("Guild %s not found – skipping", server_id)
            return

        channel = self.bot.get_channel(int(server_data.get("Channel_id", 0)))
        role_id = server_data.get("Birthday_role")
        role = discord.utils.get(guild.roles, id=int(role_id)) if role_id else None

        if not channel:
            self.logger.debug(
                "Channel not configured for server %s", server_data.get("Channel_id", 0)
            )
            return

        if not isinstance(channel, discord.channel.TextChannel):
            self.logger.error(
                "Invalid channel: channel is not a textChannel.", exc_info=True
            )
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
        role: discord.Role | None,
        user_id: str,
        user_data: dict[str, Any],
        today_key: str,
        today_full: str,
    ):
        """Process a single user's birthday status."""
        birthday = user_data.get("birthday", "")

        if not birthday:
            self.logger.debug("User %s has no birthday set", user_id)
            return

        if len(birthday) != 10:
            self.logger.warning("Invalid birthday format for user %s", user_id)
            return

        member = await safe_fetch_member(guild, int(user_id), self.logger)
        if not member:
            self.logger.debug("User %s not found in guild %s", user_id, guild.id)
            return

        user_bday_key = birthday[:5]  # Extract DD-MM

        if user_bday_key == today_key:
            self.logger.debug("It's %s's birthday today!", user_id)
            await self._handle_birthday_case(
                member, channel, role, user_data, user_id, today_full
            )
        else:
            await self._handle_regular_case(member, role)

    async def _handle_birthday_case(
        self,
        member: discord.Member,
        channel: discord.TextChannel,
        role: discord.Role | None,
        user_data: dict[str, Any],
        user_id: str,
        today_full: str,
    ) -> None:
        """Handle birthday congratulations and role management."""
        was_congrats = user_data.get("was_congrats", [])
        if today_full in was_congrats:
            self.logger.debug("User %s already congratulated today", user_id)
            return

        try:
            if role and role not in member.roles:
                if not await safe_role_edit(member, role, "add", self.logger):
                    self.logger.warning(
                        f"Failed to add birthday role to {member.id} in {member.guild.id}"
                    )

            wish = random.choice(seq=BIRTHDAY_WISHES or ["С днем рождения!"])
            embed = discord.Embed(
                title=f"ПОЗДРАВЛЕНИЯ {user_data.get('name', 'Пользователь')}",
                description=f"{wish} {member.mention}",
                color=discord.Color.gold(),
            )
            embed.set_thumbnail(url=BOT_ICON)
            await channel.send(embed=embed)

            user_data.setdefault("was_congrats", []).append(today_full)

        except Exception as e:
            self.logger.error("Failed to handle birthday for %s: %s", user_id, e)

    async def _handle_regular_case(
        self, member: discord.Member, role: discord.Role | None
    ):
        """Remove birthday role if present on non-birthdays."""
        if role and role in member.roles:
            if not await safe_role_edit(member, role, "remove", self.logger):
                self.logger.warning(
                    f"Failed to remove birthday role from {member.id} in {member.guild.id}"
                )

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
        """Set your birthday.

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
            data = get_json(BIRTHDAY_FILE) or {}
        except Exception as e:
            self.logger.error("Error loading birthday file: %s", e)
            data = {}

        author_id = str(interaction.user.id)
        guild = interaction.guild

        if guild is None:
            return await interaction.response.send_message(
                "Эта команда работает только на сервере.", ephemeral=True
            )

        server_id = str(guild.id)
        data.setdefault(
            server_id,
            {
                "Server_name": guild.name,
                "Channel_id": str(interaction.channel.id if interaction.channel else 0),
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
            msg = (
                "Ваш день рождения записан как: "
                f"{normalized_date} под именем <@{author_id}>"
            )
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
        role: discord.Role | None = None,
    ):
        """Configure birthday system for the server."""
        guild = interaction.guild

        if guild is None:
            return await interaction.response.send_message(
                "Эта команда работает только на сервере.", ephemeral=True
            )
        server_id: str = str(guild.id)

        data = get_json(BIRTHDAY_FILE) or {}
        data.setdefault(
            server_id,
            {
                "Server_name": guild.name,
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
        """Remove your birthday from the system."""
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "Эта команда работает только на сервере.", ephemeral=True
            )
        server_id = str(guild.id)
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

    @app_commands.command(
        name="list_birthdays",
        description="Список дней рождений на сервере, отсортированные по ближайшим",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(ephemeral="Скрыть сообщение после выполнения")
    async def list_birthdays(self, interaction: Interaction, ephemeral: bool = True):
        """Display all birthdays in the guild, sorted by closest to today.

        TODO: Refactor: too complex
        """
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "Эта команда работает только на сервере.", ephemeral=True
            )
        try:
            data = get_json(BIRTHDAY_FILE)
            if not isinstance(data, dict):
                self.logger.error("Top-level JSON is not an object")
                return
        except Exception as e:
            self.logger.error("Error loading birthday file: %s", e)
            return await interaction.response.send_message(
                "❌ Ошибка загрузки данных о днях рождения.", ephemeral=True
            )

        server_id = str(guild.id)
        if server_id not in data:
            return await interaction.response.send_message(
                "❌ На этом сервере нет настроенной системы дней рождений.",
                ephemeral=True,
            )

        server_data = data[server_id]
        users_data = server_data.get("Users", {})

        if not users_data:
            return await interaction.response.send_message(
                "❌ На этом сервере нет сохранённых дней рождений.", ephemeral=True
            )

        today = date.today()

        birthdays_list: list[dict[str, Any]] = []

        for user_id, user_data in users_data.items():
            birthday_str = user_data.get("birthday", "")
            if not birthday_str or len(birthday_str) != 10:
                continue

            try:
                day, month, _ = birthday_str.split("-")
                birthday1 = date(today.year, int(month), int(day))
                birthday2 = date(today.year + 1, int(month), int(day))
                days_until = (
                    (birthday1 if birthday1 >= today else birthday2) - today
                ).days
                member = await safe_fetch_member(guild, int(user_id), self.logger)
                display_name = (
                    member.mention
                    if member
                    else user_data.get("name", "Неизвестный пользователь")
                )
                month_names = {
                    1: "января",
                    2: "февраля",
                    3: "марта",
                    4: "апреля",
                    5: "мая",
                    6: "июня",
                    7: "июля",
                    8: "августа",
                    9: "сентября",
                    10: "октября",
                    11: "ноября",
                    12: "декабря",
                }
                formatted_date = f"{int(day)} {month_names[int(month)]}"
                birthdays_list.append(
                    {
                        "days_until": days_until,
                        "date": formatted_date,
                        "name": display_name,
                    }
                )
            except (ValueError, KeyError):
                self.logger.warning(
                    "Invalid birthday format for user %s: %s", user_id, birthday_str
                )
                continue
        if not birthdays_list:
            return await interaction.response.send_message(
                "❌ На этом сервере нет корректно сохранённых дней рождений.",
                ephemeral=True,
            )

        birthdays_list.sort(key=lambda x: x["days_until"])
        embed = discord.Embed(
            title=f"Дни рождения на сервере {guild.name}", color=discord.Color.gold()
        )
        lines: list[str] = []
        for i, bday in enumerate(birthdays_list, 1):
            days_text = (
                "сегодня"
                if bday["days_until"] == 0
                else f"через {bday['days_until']} д."
            )
            line = f"{i}. **{bday['date']}** - {bday['name']} ({days_text})"
            lines.append(line)

        MAX_FIELD_CHARS = 1024
        current_chunk: list[str] = []
        current_length = 0

        for line in lines:
            line_length = len(line)
            if current_length + line_length > MAX_FIELD_CHARS:
                embed.add_field(
                    name="Ближайшие дни рождения",
                    value="\n".join(current_chunk),
                    inline=False,
                )
                current_chunk = []
                current_length = 0

            current_chunk.append(line)
            current_length += line_length

        if current_chunk:
            embed.add_field(
                name="Ближайшие дни рождения",
                value="\n".join(current_chunk),
                inline=False,
            )

        embed.set_footer(text=f"Всего дней рождений: {len(birthdays_list)}")

        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)


async def setup(bot: commands.Bot):
    await bot.add_cog(BirthdayCog(bot))
