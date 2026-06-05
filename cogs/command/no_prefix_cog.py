"""Prefix blocker that suggests slash commands. Unnecessary *complicated*.

Detects old-style prefix commands and suggests modern slash command alternatives.
"""

import asyncio
import logging
import time
from datetime import timedelta

import discord
from discord import Message, app_commands
from discord.ext import commands
from discord.utils import format_dt, utcnow

from cogs.command.prefix_suggestions import (
    Command,
    Suggestion,
    SuggestionPair,
    find_prefix_suggestions,
    is_guild_command,
)


class PrefixBlockerCog(commands.Cog):
    """Redirect users from prefix commands to slash commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("PrefixBlockerCog")
        self._app_cmd_cache: dict[
            int | None, tuple[float, dict[str, app_commands.AppCommand]]
        ] = {}
        self._lock = asyncio.Lock()

    def _is_guild_command(self, cmd: Command) -> bool:
        """Determine if a command is guild-only based on available attributes."""
        return is_guild_command(cmd)

    def _requires_admin(self, cmd: Command) -> bool:
        perms = cmd.default_permissions
        if perms is not None:
            return bool(perms.administrator)

        return False

    def _get_visible_commands(self, message: Message) -> list[Command]:
        """Filter commands based on context (Guild/DM) and Permissions (Admin)."""
        visible: list[Command] = []
        is_guild = message.guild is not None

        is_admin = False
        if is_guild and isinstance(message.author, discord.Member):
            is_admin = message.author.guild_permissions.administrator

        for cmd in self.bot.tree.get_commands():
            if not is_guild and self._is_guild_command(cmd):
                continue
            if self._requires_admin(cmd) and not is_admin:
                continue
            visible.append(cmd)

        return visible

    async def _get_app_command_map(
        self, guild_id: int | None
    ) -> dict[str, app_commands.AppCommand]:
        """Fetch current AppCommands.
        Cached to avoid extra HTTP traffic.
        """
        async with self._lock:
            now = time.time()
            ttl = 600
            cached = self._app_cmd_cache.get(guild_id)
            if cached and cached[0] > now:
                return cached[1]

            global_cmds = await self.bot.tree.fetch_commands(guild=None)
            mapping: dict[str, app_commands.AppCommand] = {
                c.name: c for c in global_cmds
            }

            # Also fetch guild commands if requested
            if guild_id is not None and self.bot.get_guild(guild_id) is not None:
                guild = self.bot.get_guild(guild_id)
                if guild is not None:
                    guild_cmds = await self.bot.tree.fetch_commands(guild=guild)
                    for c in guild_cmds:
                        mapping[c.name] = c

            self._app_cmd_cache[guild_id] = (now + ttl, mapping)
            return mapping

    async def _format_clickable(
        self, *, key: str, root_name: str, message: Message
    ) -> str:
        """Prefer AppCommand mention formatting (clickable).
        For subcommands, Discord uses the root command ID: </root sub:ID>.
        """
        guild_id = message.guild.id if message.guild else None
        try:
            app_map = await self._get_app_command_map(guild_id)
            root = app_map.get(root_name)
            if root is None:
                return f"`/{key}`"
            return root.mention
        except Exception:
            return f"`/{key}`"

    @commands.Cog.listener()
    async def on_message(self, message: Message) -> None:
        if message.author.bot:
            return

        raw_content = await self._extract_prefix_query(message)
        if raw_content is None:
            return

        suggestions = find_prefix_suggestions(
            raw_content,
            self._get_visible_commands(message),
            in_guild=message.guild is not None,
        )
        response = await self._build_prefix_warning(message, suggestions)
        await self._send_prefix_warning(message, response)

    async def _extract_prefix_query(self, message: Message) -> str | None:
        prefixes = await self.bot.get_prefix(message)
        available = [prefixes] if isinstance(prefixes, str) else prefixes
        used = next((p for p in available if message.content.startswith(p)), None)
        if used is None:
            return None
        return message.content[len(used) :].strip() or None

    async def _build_prefix_warning(
        self, message: Message, suggestions: SuggestionPair
    ) -> str:
        response = "Префиксы убраны; воспользуйтесь слэш-командами."
        primary = suggestions.primary
        if primary is None:
            return response
        primary_text = await self._format_suggestion(message, primary)
        if suggestions.alternative is not None:
            alternative = await self._format_suggestion(
                message, suggestions.alternative
            )
            return (
                response + f"\n-# возможно, вы искали {primary_text} или {alternative}"
            )
        if primary.score >= 90.0:
            return response + f"\n-# попробуйте {primary_text}"
        return response + f"\n-# возможно {primary_text}"

    async def _format_suggestion(self, message: Message, suggestion: Suggestion) -> str:
        return await self._format_clickable(
            key=suggestion.key,
            root_name=suggestion.root_name,
            message=message,
        )

    async def _send_prefix_warning(self, message: Message, response: str) -> None:
        try:
            delete_after = 30
            dt = utcnow() + timedelta(seconds=delete_after)
            timer = f"-# Удалится {format_dt(dt, 'R')}"
            await message.reply(
                response + "\n\n" + timer,
                mention_author=False,
                delete_after=delete_after,
                silent=True,
            )
        except Exception as e:
            self.logger.error("Failed to send prefix warning: %s", e)


async def setup(bot: commands.Bot):
    """Setup.

    Args:
        bot: BOT ITSELF

    """
    await bot.add_cog(PrefixBlockerCog(bot))
