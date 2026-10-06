"""Blocked-user policy for command dispatch and sensitive components."""

import logging
from typing import Protocol, runtime_checkable

import discord

from api.blocking import BlockManager
from framework.error_handler import handle_app_command_error
from framework.exceptions import BlockedUserError

logger = logging.getLogger(__name__)


@runtime_checkable
class AccessClient(Protocol):
    """Client capability needed by command and component authorization."""

    @property
    def block_manager(self) -> BlockManager: ...


async def check_command_access(interaction: discord.Interaction) -> bool:
    """Reject a currently blocked guild user; allow direct-message access.

    Command owners apply intentional bypasses before calling this predicate.
    It raises ``BlockedUserError`` for the global command error handler.
    """
    if not interaction.guild:
        return True
    client = interaction.client
    if not isinstance(client, AccessClient):
        raise RuntimeError("Client has no configured access-state owner")
    if await client.block_manager.is_user_blocked(
        interaction.guild.id, interaction.user.id
    ):
        logger.debug(
            "Blocked user %s attempted an interaction in guild %s",
            interaction.user.id,
            interaction.guild.id,
        )
        raise BlockedUserError()
    return True


async def check_component_access(interaction: discord.Interaction) -> bool:
    """Check current access and route an expected denial to shared feedback.

    Components do not pass through the application-command tree's error handler.
    Return ``False`` after responding so their owners can leave the view intact.
    """
    try:
        return await check_command_access(interaction)
    except BlockedUserError as error:
        await handle_app_command_error(interaction, error)
        return False
