"""App-owned full payload records for Discord message events."""

import logging

from discord import Message

logger = logging.getLogger(__name__)


def log_message(message: Message, *, is_edit: bool = False) -> None:
    """Log the complete Discord message payload used for later analysis."""
    payload = {
        "status": "edited" if is_edit else "new",
        "message_id": message.id,
        "guild_id": message.guild.id if message.guild else None,
        "channel_id": message.channel.id,
        "author_id": message.author.id,
        "author": str(message.author),
        "content": message.content,
        "attachments": [attachment.to_dict() for attachment in message.attachments],
        "embeds": [embed.to_dict() for embed in message.embeds],
        "stickers": [
            {
                "id": sticker.id,
                "name": sticker.name,
                "format": sticker.format.name,
                "url": sticker.url,
            }
            for sticker in message.stickers
        ],
        "components": [component.to_dict() for component in message.components],
        "reference": message.reference.to_dict()
        if message.reference is not None
        else None,
        "poll": repr(message.poll) if message.poll is not None else None,
        "flags": message.flags.value,
    }
    logger.info("Discord message: %s", payload)


def log_message_edit(before: Message, after: Message) -> None:
    """Log changed fields and the complete edited payload when relevant."""
    changes: list[str] = []
    before_flags: list[str] | None = None
    after_flags: list[str] | None = None
    attr_whitelist = [
        "content",
        "embeds",
        "attachments",
        "stickers",
        "components",
        "pinned",
        "reference",
    ]
    for attr in attr_whitelist:
        before_val = getattr(before, attr, None)
        after_val = getattr(after, attr, None)
        if before_val != after_val:
            changes.append(attr)
    if before.flags.value != after.flags.value:
        before_flags = [name for name, value in before.flags if value]
        after_flags = [name for name, value in after.flags if value]
        changes.append("flags")
    if changes:
        logger.debug(
            "Message edited: %s",
            {
                "message_id": after.id,
                "guild_id": after.guild.id if after.guild else None,
                "channel_id": after.channel.id,
                "author_id": after.author.id,
                "fields": changes,
                "before_flags": before_flags,
                "after_flags": after_flags,
            },
        )
        log_message(after, is_edit=True)
