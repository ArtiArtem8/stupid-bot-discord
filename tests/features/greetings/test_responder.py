from __future__ import annotations

import secrets
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from stupidbot.app import message_logging
from stupidbot.app.message_events_cog import MessageEventsCog
from stupidbot.features.greetings.phrases import MORNING_ANSWERS


class TestGreetingPrefix(unittest.IsolatedAsyncioTestCase):
    async def test_public_greeting_uses_guild_locale_and_preserves_media_links(
        self,
    ) -> None:
        bot = MagicMock()
        bot.get_guild.return_value.preferred_locale = discord.Locale.american_english
        cog = MessageEventsCog(bot, MagicMock())
        message = MagicMock(content="доброе утро")
        message.channel.send = AsyncMock()
        for answer, expected in (
            (MORNING_ANSWERS[3], "Good morning!"),
            (MORNING_ANSWERS[0], MORNING_ANSWERS[0]),
        ):
            with (
                self.subTest(answer=answer),
                patch.object(secrets, "choice", return_value=answer),
            ):
                await cog.greetings.quest_process_message(message)
                self.assertTrue(
                    message.channel.send.call_args.args[0].startswith(expected)
                )

    async def test_string_and_sequence_ignore_only_complete_prefixes(self) -> None:
        for prefixes in ("s!", ["s!", "bot!"]):
            for content, accepted in (("!Доброе утро", True), ("s!hello", False)):
                with self.subTest(prefixes=prefixes, content=content):
                    bot = MagicMock(get_prefix=AsyncMock(return_value=prefixes))
                    cog = MessageEventsCog(bot, MagicMock())
                    message = MagicMock(content=content, guild=None)
                    message.author.bot = False
                    with (
                        patch.object(message_logging, "log_message"),
                        patch.object(
                            cog.greetings,
                            "quest_process_message",
                            new_callable=AsyncMock,
                        ) as process,
                    ):
                        await cog.on_message(message)
                    self.assertEqual(process.await_count, int(accepted))
