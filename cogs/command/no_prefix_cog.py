"""Prefix blocker that suggests slash commands. Unnecessary *complicated*.

Detects old-style prefix commands and suggests modern slash command alternatives.
"""

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import discord
from discord import Message, app_commands
from discord.ext import commands
from discord.utils import format_dt, utcnow
from rapidfuzz import fuzz, process
from rapidfuzz.utils import default_process

type CMD = (
    app_commands.ContextMenu | app_commands.Command[Any, ..., Any] | app_commands.Group
)


@dataclass(frozen=True)
class Suggestion:
    query: str
    key: str
    root_name: str
    is_guild: bool
    score: float


class PrefixBlockerCog(commands.Cog):
    """Redirect users from prefix commands to slash commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("PrefixBlockerCog")
        self._app_cmd_cache: dict[
            int | None, tuple[float, dict[str, app_commands.AppCommand]]
        ] = {}

    def _is_guild_command(self, cmd: CMD) -> bool:
        """Determine if a command is guild-only based on available attributes."""
        if cmd.guild_only:
            return True

        if cmd.allowed_contexts:
            if cmd.allowed_contexts.guild and not (
                cmd.allowed_contexts.dm_channel or cmd.allowed_contexts.private_channel
            ):
                return True

        return False

    def _requires_admin(self, cmd: CMD) -> bool:
        perms = cmd.default_permissions
        if perms is not None:
            return bool(perms.administrator)

        return False

    def _get_visible_commands(self, message: Message) -> list[CMD]:
        """Filter commands based on context (Guild/DM) and Permissions (Admin)."""
        visible: list[CMD] = []
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

    def _cutoff_for_query(self, raw_query: str) -> int:
        q = default_process(raw_query)
        if len(q) <= 1:
            return 10_000
        cutoff = 70

        if " " not in q and len(q) >= 8:
            cutoff = max(cutoff, 75)

        return cutoff

    def _flatten_commands(
        self, cmds: Iterable[CMD], *, in_guild: bool
    ) -> dict[str, tuple[CMD, str, bool]]:
        """Build match keys -> (cmd_object, root_name, is_guild_command)."""
        out: dict[str, tuple[CMD, str, bool]] = {}

        for cmd in cmds:
            is_guild_cmd = self._is_guild_command(cmd)
            out[cmd.name] = (cmd, cmd.name, is_guild_cmd)

            if isinstance(cmd, app_commands.Group):
                for sub in cmd.commands:
                    key = f"{cmd.name} {sub.name}"
                    out[key] = (sub, cmd.name, is_guild_cmd)

        return out

    async def _get_app_command_map(
        self, guild_id: int | None
    ) -> dict[str, app_commands.AppCommand]:
        """Fetch current AppCommands.
        Cached to avoid extra HTTP traffic.
        """
        now = time.time()
        ttl = 600
        cached = self._app_cmd_cache.get(guild_id)
        if cached and cached[0] > now:
            return cached[1]

        global_cmds = await self.bot.tree.fetch_commands(guild=None)
        mapping: dict[str, app_commands.AppCommand] = {c.name: c for c in global_cmds}

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
    async def on_message(self, message: Message):
        if message.author.bot:
            return

        prefixes = await self.bot.get_prefix(message)
        prefixes = [prefixes] if isinstance(prefixes, str) else prefixes

        used_prefix = next((p for p in prefixes if message.content.startswith(p)), None)
        if not used_prefix:
            return

        raw_content = message.content[len(used_prefix) :].strip()
        if not raw_content:
            return

        visible_cmds = self._get_visible_commands(message)
        in_guild = message.guild is not None

        key_map = self._flatten_commands(visible_cmds, in_guild=in_guild)
        choices = list(key_map.keys())

        suggestion: Suggestion | None = None
        alt_suggestion: Suggestion | None = None

        if choices:
            matches = process.extract(
                raw_content,
                choices,
                scorer=fuzz.WRatio,
                processor=default_process,
                limit=5,
            )

            cutoff = self._cutoff_for_query(raw_content)
            kept = [(k, float(s)) for (k, s, _idx) in matches if s >= cutoff]

            if kept:
                top_key, top_score = kept[0]
                (_, top_root, top_is_guild) = key_map[top_key]
                suggestion = Suggestion(
                    query=raw_content,
                    key=top_key,
                    root_name=top_root,
                    is_guild=top_is_guild,
                    score=top_score,
                )

                near = [(k, s) for (k, s) in kept if (top_score - s) <= 3.0]

                if len(near) >= 2 and in_guild:

                    def rank(item: tuple[str, float]) -> tuple[int, float, int]:
                        k, s = item
                        _cmd, _root, is_guild_cmd = key_map[k]
                        return (1 if is_guild_cmd else 0, s, len(k))

                    near_sorted = sorted(near, key=rank, reverse=True)
                    best_key, best_score = near_sorted[0]
                    (_, best_root, best_is_guild) = key_map[best_key]
                    suggestion = Suggestion(
                        query=raw_content,
                        key=best_key,
                        root_name=best_root,
                        is_guild=best_is_guild,
                        score=float(best_score),
                    )

                    if len(near_sorted) >= 2:
                        second_key, second_score = near_sorted[1]
                        (_, second_root, second_is_guild) = key_map[second_key]
                        alt_suggestion = Suggestion(
                            query=raw_content,
                            key=second_key,
                            root_name=second_root,
                            is_guild=second_is_guild,
                            score=float(second_score),
                        )
                else:
                    if len(kept) >= 2:
                        second_key, second_score = kept[1]
                        if (top_score - second_score) <= 8.0:
                            (_, second_root, second_is_guild) = key_map[second_key]
                            alt_suggestion = Suggestion(
                                query=raw_content,
                                key=second_key,
                                root_name=second_root,
                                is_guild=second_is_guild,
                                score=float(second_score),
                            )

        response = "Префиксы убраны; воспользуйтесь слэш-командами."
        if suggestion:
            is_sure = suggestion.score >= 90.0 and (alt_suggestion is None)
            s1 = await self._format_clickable(
                key=suggestion.key,
                root_name=suggestion.root_name,
                message=message,
            )
            if alt_suggestion:
                s2 = await self._format_clickable(
                    key=alt_suggestion.key,
                    root_name=alt_suggestion.root_name,
                    message=message,
                )
                response += f"\n-# возможно, вы искали {s1} или {s2}"
            elif is_sure:
                response += f"\n-# попробуйте {s1}"
            else:
                response += f"\n-# возможно {s1}"

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
