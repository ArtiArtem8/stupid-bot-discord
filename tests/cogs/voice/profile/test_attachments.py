"""Discord's hidden image attachments must survive a multipart message edit."""

import json
import unittest
from io import BytesIO
from unittest.mock import MagicMock

import discord
from discord.http import handle_message_parameters

from cogs.voice.profile.attachments import detail_embed, retained_attachments


def message_with_image(url: str) -> MagicMock:
    message = MagicMock(spec=discord.Message)
    message.channel.id = 42
    message.attachments = []
    message.embeds = [discord.Embed().set_image(url=url)]
    return message


class TestProfileAttachments(unittest.TestCase):
    def test_serializer_keeps_hidden_image_id_and_uploads_only_new_file(self) -> None:
        message = message_with_image(
            "https://cdn.discordapp.com/attachments/42/102/voice-xp.png?ex=123&hm=abc"
        )
        main = MagicMock(spec=discord.Attachment)
        main.id = 101
        main.to_dict.return_value = {"id": 101, "filename": "voice-profile.webp"}
        message.attachments = [main]
        file = discord.File(BytesIO(b"activity"), filename="voice-activity.png")
        try:
            retained = retained_attachments(message)
            self.assertIs(retained[0], main)
            with handle_message_parameters(
                attachments=[*retained, file],
                embeds=[detail_embed("voice-xp.png"), detail_embed(file.filename)],
            ) as params:
                if params.multipart is None:
                    self.fail("A new image must produce a multipart upload")
                payload = json.loads(params.multipart[0]["value"])
                self.assertEqual(
                    payload["attachments"],
                    [
                        {"id": 101, "filename": "voice-profile.webp"},
                        {"id": 102, "filename": "voice-xp.png"},
                        {"id": 0, "filename": "voice-activity.png"},
                    ],
                )
                self.assertEqual(params.files, [file])
                self.assertEqual(len(params.multipart), 2)
                self.assertEqual(
                    [embed["image"]["url"] for embed in payload["embeds"]],
                    ["attachment://voice-xp.png", "attachment://voice-activity.png"],
                )
                self.assertTrue(all("url" not in embed for embed in payload["embeds"]))
        finally:
            file.close()
            file.fp.close()

    def test_visible_reference_is_not_duplicated_when_also_embedded(self) -> None:
        message = message_with_image(
            "https://cdn.discordapp.com/attachments/42/102/voice-xp.png"
        )
        visible = MagicMock(spec=discord.Attachment)
        visible.id = 102
        message.attachments = [visible]
        self.assertEqual(retained_attachments(message), [visible])

    def test_ephemeral_upload_can_be_retained(self) -> None:
        message = message_with_image(
            "https://cdn.discordapp.com/ephemeral-attachments/42/102/voice-xp.png"
        )
        retained = retained_attachments(message)
        self.assertEqual(retained[0].to_dict(), {"id": 102, "filename": "voice-xp.png"})

    def test_unresolved_external_or_other_channel_images_are_rejected(self) -> None:
        urls = (
            "",
            "attachment://voice-xp.png",
            "https://example.com/attachments/42/102/voice-xp.png",
            "https://cdn.discordapp.com/attachments/43/102/voice-xp.png",
            "https://cdn.discordapp.com/attachments/42/invalid/voice-xp.png",
            "http://cdn.discordapp.com/attachments/42/102/voice-xp.png",
        )
        for url in urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                retained_attachments(message_with_image(url))
