"""Discord interactions without network access for preference workflows."""

from unittest.mock import AsyncMock, MagicMock

import discord

from stupidbot.features.access.operations import BlockManager


def interaction(user_id: int = 10) -> MagicMock:
    result = MagicMock(spec=discord.Interaction)
    result.user.id = user_id
    result.guild_id = 20
    result.locale = discord.Locale.american_english
    result.guild_locale = discord.Locale.russian
    result.extras = {}
    result.client.block_manager = MagicMock(spec=BlockManager)
    result.client.block_manager.is_user_blocked.return_value = False
    result.response = MagicMock()
    result.response.defer = AsyncMock()
    result.response.edit_message = AsyncMock()
    result.response.send_message = AsyncMock()
    result.edit_original_response = AsyncMock()
    result.permissions = discord.Permissions(manage_guild=True)
    return result
