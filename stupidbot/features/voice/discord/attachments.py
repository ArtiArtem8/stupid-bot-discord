"""Retain uploaded detail images that Discord omits from message.attachments."""

from dataclasses import dataclass
from typing import cast
from urllib.parse import unquote, urlsplit

import discord


@dataclass(frozen=True, slots=True)
class _RetainedImage:
    id: int
    filename: str

    def to_dict(self) -> dict[str, int | str]:
        return {"id": self.id, "filename": self.filename}


def detail_embed(filename: str) -> discord.Embed:
    """Create an image-only embed for an uploaded detail PNG."""
    return discord.Embed().set_image(url=f"attachment://{filename}")


def retained_attachments(message: discord.Message) -> list[discord.Attachment]:
    """Keep visible attachments and this message's embedded uploads by ID.

    discord.py's serializer accepts non-File objects through to_dict(), but its
    public annotation only accepts Attachment. The narrow cast adapts an ID-only
    retention payload without fabricating file sizes or downloading old images.
    """
    retained: dict[int, discord.Attachment | _RetainedImage] = {
        attachment.id: attachment for attachment in message.attachments
    }
    for embed in message.embeds:
        url = urlsplit(embed.image.url or "")
        parts = url.path.split("/")
        if (
            url.scheme != "https"
            or url.hostname != "cdn.discordapp.com"
            or len(parts) != 5
            or parts[1] not in {"attachments", "ephemeral-attachments"}
            or parts[2] != str(message.channel.id)
            or not parts[3].isdigit()
            or not parts[4]
        ):
            raise ValueError("Profile embed has no retainable uploaded image")
        reference = _RetainedImage(int(parts[3]), unquote(parts[4]))
        if reference.id not in retained:
            retained[reference.id] = reference
    return cast("list[discord.Attachment]", list(retained.values()))
