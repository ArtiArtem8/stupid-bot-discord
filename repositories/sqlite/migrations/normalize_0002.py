"""Frozen data conversion for revision 0003; never import runtime schema here."""

from datetime import date

from sqlalchemy import (
    Column,
    Connection,
    Integer,
    MetaData,
    String,
    Table,
    TypedColumns,
    select,
    text,
)


class OldGuildColumns(TypedColumns):
    guild_id = Column(Integer, primary_key=True)
    server_name = Column(String, nullable=False)
    channel_id = Column(Integer, nullable=False)
    birthday_role_id: Column[int | None] = Column(nullable=True)


class OldUserColumns(TypedColumns):
    guild_id = Column(Integer, primary_key=True)
    user_id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    birthday = Column(String, nullable=False)
    position = Column(Integer, nullable=False)


class OldVolumeColumns(TypedColumns):
    guild_id = Column(Integer, primary_key=True)
    volume = Column(Integer, nullable=False)


_old = MetaData()
_guilds = Table("birthday_guilds", _old, OldGuildColumns)
_users = Table("birthday_users", _old, OldUserColumns)
_volumes = Table("music_volumes", _old, OldVolumeColumns)


def _guild(connection: Connection, guild_id: int) -> None:
    if isinstance(guild_id, bool) or not 0 < guild_id < 2**63:
        raise ValueError("Unsupported legacy guild ID")
    connection.execute(
        text("INSERT OR IGNORE INTO guilds(guild_id) VALUES (:id)"), {"id": guild_id}
    )


def _birth_date(value: str) -> str | None:
    if not value:
        return None
    parts = value.split("-")
    if len(parts) != 3 or len(value) != 10:
        raise ValueError("Resolve invalid legacy birthday before migration")
    day, month, year = map(int, parts)
    return date(year, month, day).isoformat()


def _birthdays(connection: Connection) -> None:
    rows = connection.execute(
        select(
            _guilds.c.guild_id,
            _guilds.c.server_name,
            _guilds.c.channel_id,
            _guilds.c.birthday_role_id,
        )
    )
    for guild_id, name, channel, role in rows:
        _guild(connection, guild_id)
        if channel:
            connection.execute(
                text(
                    """INSERT OR IGNORE INTO channels(channel_id,guild_id)
                    VALUES (:id,:guild)"""
                ),
                {"id": channel, "guild": guild_id},
            )
        if role is not None:
            connection.execute(
                text(
                    "INSERT OR IGNORE INTO roles(role_id,guild_id) VALUES (:id,:guild)"
                ),
                {"id": role, "guild": guild_id},
            )
        connection.execute(
            text(
                "INSERT INTO birthday_settings VALUES (:guild,:channel,:role,:name,1)"
            ),
            {"guild": guild_id, "channel": channel or None, "role": role, "name": name},
        )
    members = connection.execute(
        select(
            _users.c.guild_id,
            _users.c.user_id,
            _users.c.name,
            _users.c.birthday,
            _users.c.position,
        )
    )
    for guild_id, user_id, name, birthday, position in members:
        connection.execute(
            text("INSERT OR IGNORE INTO users(user_id) VALUES (:id)"), {"id": user_id}
        )
        connection.execute(
            text(
                "INSERT OR IGNORE INTO guild_members(guild_id,user_id) "
                + "VALUES (:guild,:user)"
            ),
            {"guild": guild_id, "user": user_id},
        )
        connection.execute(
            text(
                """INSERT INTO member_birthdays
                VALUES (:guild,:user,:birthday,:position,:name,1)"""
            ),
            {
                "guild": guild_id,
                "user": user_id,
                "birthday": _birth_date(birthday),
                "position": position,
                "name": name,
            },
        )
    connection.execute(
        text(
            """INSERT INTO birthday_history
            SELECT guild_id,user_id,position,value,'legacy'
            FROM birthday_congratulations"""
        )
    )


def transfer_existing(connection: Connection) -> None:
    """Normalize existing feature data atomically and remove old feature tables."""
    _birthdays(connection)
    for guild_id, volume in connection.execute(
        select(_volumes.c.guild_id, _volumes.c.volume)
    ):
        _guild(connection, guild_id)
        connection.execute(
            text("INSERT INTO music_settings VALUES (:guild,:volume,1)"),
            {"guild": guild_id, "volume": volume},
        )
    connection.execute(
        text("INSERT INTO storage_state VALUES (1,'COMPLETE',NULL,'schema_upgrade')")
    )
    connection.execute(text("INSERT INTO voice_shared_revision VALUES (1,0,0)"))
    connection.execute(text("DROP TABLE birthday_congratulations"))
    connection.execute(text("DROP TABLE birthday_users"))
    connection.execute(text("DROP TABLE birthday_guilds"))
    connection.execute(text("DROP TABLE music_volumes"))
