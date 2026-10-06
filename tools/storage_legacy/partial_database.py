"""Read the explicitly selected earlier SQLite pilot without modifying its schema."""

from pathlib import Path

from sqlalchemy import URL, String, create_engine, select, text

from api.birthday_models import BirthdayGuildConfig, BirthdayUser
from repositories.volume_repository import VolumeData
from tools.storage_legacy.partial_schema import congratulations, guilds, users, volumes


def read_partial(path: Path) -> tuple[list[BirthdayGuildConfig], list[VolumeData], str]:
    """Read only revision 0001/0002; callers must choose it explicitly per feature."""
    engine = create_engine(
        URL.create(
            "sqlite+pysqlite",
            database=path.resolve().as_uri(),
            query={"mode": "ro", "immutable": "1", "uri": "true"},
        )
    )
    try:
        with engine.connect() as connection:
            revisions = list(
                connection.scalars(
                    text("SELECT version_num FROM alembic_version").columns(
                        version_num=String
                    )
                )
            )
            if len(revisions) != 1 or revisions[0] not in (
                "0001_birthdays",
                "0002_music_volume",
            ):
                raise ValueError(
                    "Partial SQLite source must be at revision 0001 or 0002"
                )
            configs = {
                gid: BirthdayGuildConfig(
                    gid, name, channel or None, birthday_role_id=role
                )
                for gid, name, channel, role in connection.execute(
                    select(
                        guilds.c.guild_id,
                        guilds.c.server_name,
                        guilds.c.channel_id,
                        guilds.c.birthday_role_id,
                    )
                )
            }
            for gid, uid, name, birthday in connection.execute(
                select(
                    users.c.guild_id, users.c.user_id, users.c.name, users.c.birthday
                ).order_by(users.c.guild_id, users.c.position)
            ):
                configs[gid].users[uid] = BirthdayUser(uid, name, birthday)
            for gid, uid, value in connection.execute(
                select(
                    congratulations.c.guild_id,
                    congratulations.c.user_id,
                    congratulations.c.value,
                ).order_by(
                    congratulations.c.guild_id,
                    congratulations.c.user_id,
                    congratulations.c.position,
                )
            ):
                configs[gid].users[uid].was_congrats.append(value)
            settings = (
                [
                    VolumeData(gid, volume)
                    for gid, volume in connection.execute(
                        select(volumes.c.guild_id, volumes.c.volume)
                    )
                ]
                if revisions[0] == "0002_music_volume"
                else []
            )
            return list(configs.values()), settings, revisions[0]
    finally:
        engine.dispose()
