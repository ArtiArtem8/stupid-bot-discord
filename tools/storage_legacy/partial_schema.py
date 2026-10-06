"""Read-only descriptions of the earlier birthday and volume pilot tables."""

from sqlalchemy import (
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Table,
    TypedColumns,
)

metadata = MetaData()


class VolumeColumns(TypedColumns):
    guild_id = Column(Integer, primary_key=True, autoincrement=False)
    volume = Column(Integer, nullable=False)


volumes = Table("music_volumes", metadata, VolumeColumns)


class GuildColumns(TypedColumns):
    guild_id = Column(Integer, primary_key=True, autoincrement=False)
    server_name = Column(String, nullable=False)
    channel_id = Column(Integer, nullable=False)
    birthday_role_id: Column[int | None] = Column(nullable=True)


class UserColumns(TypedColumns):
    guild_id = Column(
        Integer,
        ForeignKey("birthday_guilds.guild_id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id = Column(Integer, primary_key=True, autoincrement=False)
    name = Column(String, nullable=False)
    birthday = Column(String, nullable=False)
    position = Column(Integer, nullable=False)


class CongratulationColumns(TypedColumns):
    guild_id = Column(Integer, primary_key=True)
    user_id = Column(Integer, primary_key=True)
    position = Column(Integer, primary_key=True)
    value = Column(String, nullable=False)


guilds = Table("birthday_guilds", metadata, GuildColumns)
users = Table("birthday_users", metadata, UserColumns)
congratulations = Table(
    "birthday_congratulations",
    metadata,
    CongratulationColumns,
    ForeignKeyConstraint(
        ["guild_id", "user_id"],
        ["birthday_users.guild_id", "birthday_users.user_id"],
        ondelete="CASCADE",
    ),
)
