"""Static regression checks for Core results; checked by both project analyzers."""

from typing import assert_type

from sqlalchemy import Row, RowMapping, Select, select
from sqlalchemy.ext.asyncio import AsyncConnection

from api.birthday_models import BirthdayGuildConfig
from repositories.sqlite.schema import birthday_settings as guilds
from repositories.sqlite.schema import voice_record_states, voice_records
from repositories.voice_repository import _column_value


async def read_config(connection: AsyncConnection) -> BirthdayGuildConfig:
    """Verify concrete query and result types, including the nullable role."""
    statement = select(
        guilds.c.guild_id,
        guilds.c.guild_name_hint,
        guilds.c.channel_id,
        guilds.c.birthday_role_id,
    )
    assert_type(statement, Select[int, str, int | None, int | None])
    result = await connection.execute(statement)
    row = result.one()
    assert_type(row, Row[int, str, int | None, int | None])
    guild_id, name, channel_id, role_id = row
    assert_type(guild_id, int)
    assert_type(name, str)
    assert_type(channel_id, int | None)
    assert_type(role_id, int | None)
    return BirthdayGuildConfig(guild_id, name, channel_id, birthday_role_id=role_id)


def check_voice_columns(row: RowMapping) -> None:
    assert_type(_column_value(row, voice_records.c.record_id), int)
    assert_type(_column_value(row, voice_records.c.boot_id), str)
    assert_type(_column_value(row, voice_records.c.monotonic), float)
    assert_type(_column_value(row, voice_records.c.authoritative), bool | None)
    assert_type(_column_value(row, voice_record_states.c.channel_id), int | None)
    assert_type(_column_value(row, voice_record_states.c.channel_known), bool)
    assert_type(_column_value(row, voice_record_states.c.session_id), str | None)
