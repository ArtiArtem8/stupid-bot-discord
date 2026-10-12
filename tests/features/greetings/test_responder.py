from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from stupidbot.app import message_logging
from stupidbot.app.message_events_cog import MessageEventsCog


class TestGreetingPrefix(unittest.IsolatedAsyncioTestCase):
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
