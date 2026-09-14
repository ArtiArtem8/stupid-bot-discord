from __future__ import annotations

import unittest
from typing import cast, override
from unittest.mock import MagicMock, patch

from cogs.on_message_cog import OnMessageCog


class TestMessageLogging(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.cog = OnMessageCog(MagicMock())

    def _message(self, content: str) -> MagicMock:
        message = MagicMock()
        message.id = 101
        message.guild.id = 202
        message.channel.id = 303
        message.author.id = 404
        message.author.__str__.return_value = "author-name"
        message.content = content

        attachment = MagicMock()
        attachment.to_dict.return_value = {
            "id": 505,
            "filename": "data.txt",
            "url": "https://cdn.example.test/data.txt",
        }
        message.attachments = [attachment]
        message.embeds = []
        message.stickers = []
        message.components = []
        message.reference.to_dict.return_value = {
            "message_id": 606,
            "channel_id": 303,
            "guild_id": 202,
        }
        message.poll = None
        message.flags.value = 0
        return message

    def test_new_message_log_contains_full_payload(self) -> None:
        message = self._message("full message content for analysis")

        with patch("cogs.on_message_cog.logger.info") as log_info:
            self.cog._log_message(message)

        payload = cast(dict[str, object], log_info.call_args.args[1])
        self.assertEqual(payload["status"], "new")
        self.assertEqual(payload["content"], message.content)
        self.assertEqual(payload["author"], "author-name")
        self.assertEqual(payload["author_id"], 404)
        self.assertEqual(
            cast(list[dict[str, object]], payload["attachments"])[0]["url"],
            "https://cdn.example.test/data.txt",
        )
        self.assertEqual(
            cast(dict[str, object], payload["reference"])["message_id"],
            606,
        )

    def test_edited_message_log_contains_new_full_content(self) -> None:
        message = self._message("complete edited content")

        with patch("cogs.on_message_cog.logger.info") as log_info:
            self.cog._log_message(message, is_edit=True)

        payload = cast(dict[str, object], log_info.call_args.args[1])
        self.assertEqual(payload["status"], "edited")
        self.assertEqual(payload["content"], "complete edited content")

    def test_fuzzy_match_log_contains_message_text(self) -> None:
        message = self._message("доброе утро")

        with patch("cogs.on_message_cog.logger.info") as log_info:
            answer = self.cog.process_fuzzy_message(
                message,
                ["доброе утро"],
                ["И тебе"],
            )

        self.assertEqual(answer, "И тебе")
        log_args = log_info.call_args.args
        rendered = cast(str, log_args[0]) % log_args[1:]
        self.assertIn("'content': 'доброе утро'", rendered)
