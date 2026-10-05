"""Static regression checks for Core results; checked by both project analyzers."""

from typing import assert_type

from sqlalchemy import Row, Select, select
from sqlalchemy.ext.asyncio import AsyncConnection

from api.birthday_models import BirthdayGuildConfig
from repositories.birthday_sqlite.schema import guilds


async def read_config(connection: AsyncConnection) -> BirthdayGuildConfig:
    """Verify concrete query and result types, including the nullable role."""
    statement = select(
        guilds.c.guild_id,
        guilds.c.server_name,
        guilds.c.channel_id,
        guilds.c.birthday_role_id,
    )
    assert_type(statement, Select[int, str, int, int | None])
    result = await connection.execute(statement)
    row = result.one()
    assert_type(row, Row[int, str, int, int | None])
    guild_id, name, channel_id, role_id = row
    assert_type(guild_id, int)
    assert_type(name, str)
    assert_type(channel_id, int)
    assert_type(role_id, int | None)
    return BirthdayGuildConfig(guild_id, name, channel_id, birthday_role_id=role_id)
