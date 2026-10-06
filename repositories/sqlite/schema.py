"""Normalized durable application tables; all identities are positive integers."""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    TypedColumns,
    UniqueConstraint,
)

metadata = MetaData()


class UsersColumns(TypedColumns):
    user_id = Column(Integer, primary_key=True, autoincrement=False)
    username: Column[str | None] = Column(nullable=True)
    global_name: Column[str | None] = Column(nullable=True)
    is_bot: Column[bool | None] = Column(nullable=True)
    observed_us: Column[int | None] = Column(nullable=True)


users = Table(
    "users",
    metadata,
    UsersColumns,
    CheckConstraint("user_id > 0"),
    CheckConstraint("is_bot IS NULL OR is_bot IN (0, 1)"),
)


class GuildsColumns(TypedColumns):
    guild_id = Column(Integer, primary_key=True, autoincrement=False)
    name: Column[str | None] = Column(nullable=True)
    observed_us: Column[int | None] = Column(nullable=True)


guilds = Table(
    "guilds",
    metadata,
    GuildsColumns,
    CheckConstraint("guild_id > 0"),
)


class GuildMembersColumns(TypedColumns):
    guild_id = Column(Integer, primary_key=True, autoincrement=False)
    user_id = Column(Integer, primary_key=True, autoincrement=False)
    nickname: Column[str | None] = Column(nullable=True)
    observed_us: Column[int | None] = Column(nullable=True)


guild_members = Table(
    "guild_members",
    metadata,
    GuildMembersColumns,
    ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"]),
    ForeignKeyConstraint(["user_id"], ["users.user_id"]),
)
Index("ix_members_user_guild", guild_members.c.user_id, guild_members.c.guild_id)


class ChannelsColumns(TypedColumns):
    channel_id = Column(Integer, primary_key=True, autoincrement=False)
    guild_id: Column[int | None] = Column(nullable=True)
    name: Column[str | None] = Column(nullable=True)
    kind: Column[str | None] = Column(nullable=True)
    observed_us: Column[int | None] = Column(nullable=True)


channels = Table(
    "channels",
    metadata,
    ChannelsColumns,
    CheckConstraint("channel_id > 0"),
    ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"]),
    UniqueConstraint("channel_id", "guild_id"),
)


class RolesColumns(TypedColumns):
    role_id = Column(Integer, primary_key=True, autoincrement=False)
    guild_id = Column(Integer, nullable=False)
    name: Column[str | None] = Column(nullable=True)
    deleted_us: Column[int | None] = Column(nullable=True)


roles = Table(
    "roles",
    metadata,
    RolesColumns,
    CheckConstraint("role_id > 0"),
    ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"]),
    UniqueConstraint("role_id", "guild_id"),
)
Index("ix_roles_guild", roles.c.guild_id)


class StorageStateColumns(TypedColumns):
    singleton = Column(Integer, primary_key=True, autoincrement=False)
    status = Column(String, nullable=False)
    manifest_hash: Column[str | None] = Column(nullable=True)
    origin = Column(String, nullable=False)


storage_state = Table(
    "storage_state",
    metadata,
    StorageStateColumns,
    CheckConstraint("singleton = 1"),
    CheckConstraint("status IN ('BUILDING','COMPLETE')"),
)


class MigrationSourcesColumns(TypedColumns):
    source_name = Column(String, primary_key=True)
    digest = Column(String, nullable=False)
    record_count = Column(Integer, nullable=False)
    schema_version = Column(String, nullable=False)
    bounds_hint: Column[str | None] = Column(nullable=True)


migration_sources = Table(
    "migration_sources",
    metadata,
    MigrationSourcesColumns,
    CheckConstraint("record_count >= 0"),
)


class BirthdaySettingsColumns(TypedColumns):
    guild_id = Column(Integer, primary_key=True, autoincrement=False)
    channel_id: Column[int | None] = Column(nullable=True)
    birthday_role_id: Column[int | None] = Column(nullable=True)
    guild_name_hint = Column(String, nullable=False)
    version = Column(Integer, nullable=False)


birthday_settings = Table(
    "birthday_settings",
    metadata,
    BirthdaySettingsColumns,
    ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"]),
    ForeignKeyConstraint(
        ["channel_id", "guild_id"], ["channels.channel_id", "channels.guild_id"]
    ),
    ForeignKeyConstraint(
        ["birthday_role_id", "guild_id"], ["roles.role_id", "roles.guild_id"]
    ),
    CheckConstraint("version > 0"),
)


class MemberBirthdaysColumns(TypedColumns):
    guild_id = Column(Integer, primary_key=True, autoincrement=False)
    user_id = Column(Integer, primary_key=True, autoincrement=False)
    birth_date: Column[str | None] = Column(nullable=True)
    position = Column(Integer, nullable=False)
    display_name_hint = Column(String, nullable=False)
    version = Column(Integer, nullable=False)


member_birthdays = Table(
    "member_birthdays",
    metadata,
    MemberBirthdaysColumns,
    ForeignKeyConstraint(
        ["guild_id", "user_id"], ["guild_members.guild_id", "guild_members.user_id"]
    ),
    CheckConstraint("version > 0"),
    CheckConstraint("position >= 0"),
    CheckConstraint(
        "birth_date IS NULL OR (length(birth_date) = 10 AND "
        + "date(birth_date, '+0 days') IS NOT NULL AND "
        + "date(birth_date, '+0 days') = birth_date)"
    ),
)
Index(
    "ix_birthdays_user_guild", member_birthdays.c.user_id, member_birthdays.c.guild_id
)


class BirthdayHistoryColumns(TypedColumns):
    guild_id = Column(Integer, primary_key=True, autoincrement=False)
    user_id = Column(Integer, primary_key=True, autoincrement=False)
    position = Column(Integer, primary_key=True, autoincrement=False)
    value = Column(String, nullable=False)
    origin = Column(String, nullable=False)


birthday_history = Table(
    "birthday_history",
    metadata,
    BirthdayHistoryColumns,
    ForeignKeyConstraint(
        ["guild_id", "user_id"],
        ["member_birthdays.guild_id", "member_birthdays.user_id"],
        ondelete="CASCADE",
    ),
    CheckConstraint("position >= 0"),
)


class BirthdayDeliveriesColumns(TypedColumns):
    guild_id = Column(Integer, primary_key=True, autoincrement=False)
    user_id = Column(Integer, primary_key=True, autoincrement=False)
    calendar_date = Column(String, primary_key=True)
    operation_id = Column(String, nullable=False)
    settings_version = Column(Integer, nullable=False)
    birthday_version = Column(Integer, nullable=False)
    status = Column(String, nullable=False)
    message_id: Column[int | None] = Column(nullable=True)
    updated_us = Column(Integer, nullable=False)


birthday_deliveries = Table(
    "birthday_deliveries",
    metadata,
    BirthdayDeliveriesColumns,
    ForeignKeyConstraint(
        ["guild_id", "user_id"], ["guild_members.guild_id", "guild_members.user_id"]
    ),
    UniqueConstraint("operation_id"),
    CheckConstraint("status IN ('claimed','sent','uncertain','obsolete')"),
    CheckConstraint("message_id IS NULL OR message_id > 0"),
)


class MusicSettingsColumns(TypedColumns):
    guild_id = Column(Integer, primary_key=True, autoincrement=False)
    volume = Column(Integer, nullable=False)
    version = Column(Integer, nullable=False)


music_settings = Table(
    "music_settings",
    metadata,
    MusicSettingsColumns,
    ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"]),
    CheckConstraint("volume >= 0 AND volume <= 200"),
    CheckConstraint("version > 0"),
)


class MemberBlocksColumns(TypedColumns):
    guild_id = Column(Integer, primary_key=True, autoincrement=False)
    user_id = Column(Integer, primary_key=True, autoincrement=False)
    blocked = Column(Boolean, nullable=False)
    display_name_hint = Column(String, nullable=False)
    username_hint: Column[str | None] = Column(nullable=True)
    version = Column(Integer, nullable=False)


member_blocks = Table(
    "member_blocks",
    metadata,
    MemberBlocksColumns,
    ForeignKeyConstraint(
        ["guild_id", "user_id"], ["guild_members.guild_id", "guild_members.user_id"]
    ),
    CheckConstraint("version > 0"),
    CheckConstraint("blocked IN (0, 1)"),
)


class BlockEventsColumns(TypedColumns):
    event_id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    action = Column(String, nullable=False)
    ordinal = Column(Integer, nullable=False)
    admin_id = Column(Integer, nullable=False)
    reason: Column[str | None] = Column(nullable=True)
    created_us = Column(Integer, nullable=False)
    origin = Column(String, nullable=False)


block_events = Table(
    "block_events",
    metadata,
    BlockEventsColumns,
    ForeignKeyConstraint(
        ["guild_id", "user_id"], ["guild_members.guild_id", "guild_members.user_id"]
    ),
    ForeignKeyConstraint(["admin_id"], ["users.user_id"]),
    UniqueConstraint("guild_id", "user_id", "action", "ordinal"),
    CheckConstraint("action IN ('block','unblock')"),
    sqlite_autoincrement=True,
)
Index(
    "ix_block_events_member",
    block_events.c.guild_id,
    block_events.c.user_id,
    block_events.c.event_id,
)
Index("ix_block_events_admin", block_events.c.admin_id)


class MemberNameObservationsColumns(TypedColumns):
    guild_id = Column(Integer, primary_key=True, autoincrement=False)
    user_id = Column(Integer, primary_key=True, autoincrement=False)
    ordinal = Column(Integer, primary_key=True, autoincrement=False)
    display_name = Column(String, nullable=False)
    created_us = Column(Integer, nullable=False)


member_name_observations = Table(
    "member_name_observations",
    metadata,
    MemberNameObservationsColumns,
    ForeignKeyConstraint(
        ["guild_id", "user_id"], ["guild_members.guild_id", "guild_members.user_id"]
    ),
)


class ReportSettingsColumns(TypedColumns):
    singleton = Column(Integer, primary_key=True, autoincrement=False)
    channel_id: Column[int | None] = Column(nullable=True)
    version = Column(Integer, nullable=False)


report_settings = Table(
    "report_settings",
    metadata,
    ReportSettingsColumns,
    CheckConstraint("singleton = 1"),
    ForeignKeyConstraint(["channel_id"], ["channels.channel_id"]),
    CheckConstraint("version > 0"),
)


class ReportsColumns(TypedColumns):
    report_id = Column(String, primary_key=True)
    position = Column(Integer, nullable=False, unique=True)
    request_key: Column[str | None] = Column(nullable=True)
    user_id = Column(Integer, nullable=False)
    guild_id: Column[int | None] = Column(nullable=True)
    channel_id: Column[int | None] = Column(nullable=True)
    reason = Column(String, nullable=False)
    created_us: Column[int | None] = Column(nullable=True)
    created_text: Column[str | None] = Column(nullable=True)
    user_name_at_event = Column(String, nullable=False)
    avatar_at_event: Column[str | None] = Column(nullable=True)
    guild_name_at_event: Column[str | None] = Column(nullable=True)
    channel_name_at_event: Column[str | None] = Column(nullable=True)
    provenance = Column(String, nullable=False)


reports = Table(
    "reports",
    metadata,
    ReportsColumns,
    ForeignKeyConstraint(["user_id"], ["users.user_id"]),
    ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"]),
    ForeignKeyConstraint(["channel_id"], ["channels.channel_id"]),
    UniqueConstraint("request_key"),
    CheckConstraint("position >= 0"),
)
Index("ix_reports_user", reports.c.user_id, reports.c.report_id)
Index("ix_reports_guild", reports.c.guild_id, reports.c.report_id)
Index("ix_reports_channel", reports.c.channel_id)


class QuestionAnswersColumns(TypedColumns):
    user_id = Column(Integer, primary_key=True, autoincrement=False)
    normalized_question = Column(String, primary_key=True)
    answer = Column(String, nullable=False)


question_answers = Table(
    "question_answers",
    metadata,
    QuestionAnswersColumns,
    ForeignKeyConstraint(["user_id"], ["users.user_id"]),
)


class MonitorSettingsColumns(TypedColumns):
    guild_id = Column(Integer, primary_key=True, autoincrement=False)
    enabled = Column(Boolean, nullable=False)
    ttl_days: Column[int | None] = Column(nullable=True)
    version = Column(Integer, nullable=False)


monitor_settings = Table(
    "monitor_settings",
    metadata,
    MonitorSettingsColumns,
    ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"]),
    CheckConstraint("ttl_days IS NULL OR ttl_days > 0"),
    CheckConstraint("version > 0"),
    CheckConstraint("enabled IN (0, 1)"),
)


class RoleSnapshotsColumns(TypedColumns):
    snapshot_id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    left_us = Column(Integer, nullable=False)
    username_at_leave = Column(String, nullable=False)
    version = Column(Integer, nullable=False)


role_snapshots = Table(
    "role_snapshots",
    metadata,
    RoleSnapshotsColumns,
    ForeignKeyConstraint(
        ["guild_id", "user_id"], ["guild_members.guild_id", "guild_members.user_id"]
    ),
    UniqueConstraint("guild_id", "user_id"),
    UniqueConstraint("snapshot_id", "guild_id"),
    CheckConstraint("version > 0"),
    sqlite_autoincrement=True,
)


class RoleSnapshotRolesColumns(TypedColumns):
    snapshot_id = Column(Integer, primary_key=True, autoincrement=False)
    guild_id = Column(Integer, nullable=False)
    role_id = Column(Integer, nullable=False)
    position = Column(Integer, primary_key=True, autoincrement=False)


role_snapshot_roles = Table(
    "role_snapshot_roles",
    metadata,
    RoleSnapshotRolesColumns,
    ForeignKeyConstraint(
        ["snapshot_id", "guild_id"],
        ["role_snapshots.snapshot_id", "role_snapshots.guild_id"],
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(["role_id", "guild_id"], ["roles.role_id", "roles.guild_id"]),
)
Index(
    "ix_snapshot_roles_role",
    role_snapshot_roles.c.role_id,
    role_snapshot_roles.c.guild_id,
)


class UptimePeriodsColumns(TypedColumns):
    period_id = Column(Integer, primary_key=True, autoincrement=True)
    started_us: Column[int | None] = Column(nullable=True)
    accumulated_us = Column(Integer, nullable=False)
    last_checkpoint_us = Column(Integer, nullable=False)
    archived_us: Column[int | None] = Column(nullable=True)
    reset_reason: Column[str | None] = Column(nullable=True)
    origin = Column(String, nullable=False)


uptime_periods = Table(
    "uptime_periods",
    metadata,
    UptimePeriodsColumns,
    CheckConstraint("accumulated_us >= 0"),
    CheckConstraint("(archived_us IS NULL) = (reset_reason IS NULL)"),
    sqlite_autoincrement=True,
)


class RuntimeCheckpointColumns(TypedColumns):
    singleton = Column(Integer, primary_key=True, autoincrement=False)
    checkpoint_us = Column(Integer, nullable=False)
    accumulated_us = Column(Integer, nullable=False)
    origin = Column(String, nullable=False)
    boot_id: Column[str | None] = Column(nullable=True)
    period_id = Column(Integer, nullable=False)


runtime_checkpoint = Table(
    "runtime_checkpoint",
    metadata,
    RuntimeCheckpointColumns,
    ForeignKeyConstraint(["period_id"], ["uptime_periods.period_id"]),
    CheckConstraint("singleton = 1"),
    CheckConstraint("accumulated_us >= 0"),
    CheckConstraint("origin IN ('legacy_checkpoint','startup','autosave','shutdown')"),
)


class VoiceBatchesColumns(TypedColumns):
    batch_id = Column(String, primary_key=True)
    fingerprint = Column(String, nullable=False)
    record_count = Column(Integer, nullable=False)
    revision = Column(Integer, nullable=False)


voice_batches = Table(
    "voice_batches",
    metadata,
    VoiceBatchesColumns,
    CheckConstraint("record_count >= 0"),
    CheckConstraint("revision >= 0"),
)


class VoiceRecordsColumns(TypedColumns):
    # SQLAlchemy requires an instance annotation on this never-instantiated class.
    __row_pos__: tuple[  # pyright: ignore[reportUninitializedInstanceVariable]
        int,
        str,
        int,
        str,
        int,
        int,
        float,
        str,
        int | None,
        bool | None,
        bool | None,
        int | None,
        int | None,
        str | None,
        bool | None,
    ]
    record_id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(String, nullable=False)
    ordinal = Column(Integer, nullable=False)
    boot_id = Column(String, nullable=False)
    sequence = Column(Integer, nullable=False)
    observed_us = Column(Integer, nullable=False)
    monotonic: Column[float] = Column(Float, nullable=False)
    kind = Column(String, nullable=False)
    guild_id: Column[int | None] = Column(nullable=True)
    authoritative: Column[bool | None] = Column(nullable=True)
    stopped: Column[bool | None] = Column(nullable=True)
    gap_start_us: Column[int | None] = Column(nullable=True)
    gap_end_us: Column[int | None] = Column(nullable=True)
    gap_reason: Column[str | None] = Column(nullable=True)
    known_bounds: Column[bool | None] = Column(nullable=True)


voice_records = Table(
    "voice_records",
    metadata,
    VoiceRecordsColumns,
    ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"]),
    ForeignKeyConstraint(["batch_id"], ["voice_batches.batch_id"]),
    UniqueConstraint("batch_id", "ordinal"),
    UniqueConstraint("record_id", "guild_id"),
    CheckConstraint("sequence >= 0"),
    CheckConstraint(
        "kind IN ('observation','snapshot','gap','checkpoint','lifecycle')"
    ),
    CheckConstraint("(kind = 'snapshot') = (authoritative IS NOT NULL)"),
    CheckConstraint("(kind = 'lifecycle') = (stopped IS NOT NULL)"),
    CheckConstraint(
        "(kind = 'gap') = (gap_start_us IS NOT NULL AND gap_reason IS "
        + "NOT NULL AND known_bounds IS NOT NULL)"
    ),
    CheckConstraint("kind NOT IN ('observation','snapshot') OR guild_id IS NOT NULL"),
    CheckConstraint("gap_end_us IS NULL OR gap_end_us >= gap_start_us"),
    CheckConstraint("authoritative IS NULL OR authoritative IN (0, 1)"),
    CheckConstraint("stopped IS NULL OR stopped IN (0, 1)"),
    CheckConstraint("known_bounds IS NULL OR known_bounds IN (0, 1)"),
    sqlite_autoincrement=True,
)
Index("ix_voice_scope_cursor", voice_records.c.guild_id, voice_records.c.record_id)
Index(
    "ix_voice_replay",
    voice_records.c.boot_id,
    voice_records.c.sequence,
    voice_records.c.kind,
    voice_records.c.record_id,
)


class VoiceRecordStatesColumns(TypedColumns):
    # SQLAlchemy requires an instance annotation on this never-instantiated class.
    __row_pos__: tuple[  # pyright: ignore[reportUninitializedInstanceVariable]
        int,
        int,
        int,
        int,
        int | None,
        bool,
        bool | None,
        bool | None,
        bool | None,
        bool | None,
        bool | None,
        bool | None,
        bool | None,
        bool | None,
        bool | None,
        bool | None,
        int | None,
        str | None,
    ]
    record_id = Column(Integer, primary_key=True, autoincrement=False)
    position = Column(Integer, primary_key=True, autoincrement=False)
    guild_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    channel_id: Column[int | None] = Column(nullable=True)
    channel_known = Column(Boolean, nullable=False)
    is_bot: Column[bool | None] = Column(nullable=True)
    self_mute: Column[bool | None] = Column(nullable=True)
    self_deaf: Column[bool | None] = Column(nullable=True)
    server_mute: Column[bool | None] = Column(nullable=True)
    server_deaf: Column[bool | None] = Column(nullable=True)
    self_stream: Column[bool | None] = Column(nullable=True)
    self_video: Column[bool | None] = Column(nullable=True)
    suppress: Column[bool | None] = Column(nullable=True)
    afk: Column[bool | None] = Column(nullable=True)
    requested_to_speak: Column[bool | None] = Column(nullable=True)
    requested_us: Column[int | None] = Column(nullable=True)
    session_id: Column[str | None] = Column(nullable=True)


voice_record_states = Table(
    "voice_record_states",
    metadata,
    VoiceRecordStatesColumns,
    ForeignKeyConstraint(
        ["record_id", "guild_id"],
        ["voice_records.record_id", "voice_records.guild_id"],
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["guild_id", "user_id"], ["guild_members.guild_id", "guild_members.user_id"]
    ),
    ForeignKeyConstraint(
        ["channel_id", "guild_id"], ["channels.channel_id", "channels.guild_id"]
    ),
    UniqueConstraint("record_id", "user_id"),
    CheckConstraint("position >= 0"),
    CheckConstraint("requested_us IS NULL OR requested_to_speak = 1"),
    CheckConstraint("channel_known IN (0, 1)"),
    CheckConstraint("is_bot IS NULL OR is_bot IN (0, 1)"),
    CheckConstraint("self_mute IS NULL OR self_mute IN (0, 1)"),
    CheckConstraint("self_deaf IS NULL OR self_deaf IN (0, 1)"),
    CheckConstraint("server_mute IS NULL OR server_mute IN (0, 1)"),
    CheckConstraint("server_deaf IS NULL OR server_deaf IN (0, 1)"),
    CheckConstraint("self_stream IS NULL OR self_stream IN (0, 1)"),
    CheckConstraint("self_video IS NULL OR self_video IN (0, 1)"),
    CheckConstraint("suppress IS NULL OR suppress IN (0, 1)"),
    CheckConstraint("afk IS NULL OR afk IN (0, 1)"),
    CheckConstraint("requested_to_speak IS NULL OR requested_to_speak IN (0, 1)"),
)
Index(
    "ix_voice_state_user_record",
    voice_record_states.c.user_id,
    voice_record_states.c.record_id,
)
Index(
    "ix_voice_state_member",
    voice_record_states.c.guild_id,
    voice_record_states.c.user_id,
)
Index(
    "ix_voice_state_channel",
    voice_record_states.c.channel_id,
    voice_record_states.c.guild_id,
)


class VoiceRevisionsColumns(TypedColumns):
    guild_id = Column(Integer, primary_key=True, autoincrement=False)
    revision = Column(Integer, nullable=False)


voice_revisions = Table(
    "voice_revisions",
    metadata,
    VoiceRevisionsColumns,
    ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"]),
    CheckConstraint("revision >= 0"),
)


class VoiceSharedRevisionColumns(TypedColumns):
    singleton = Column(Integer, primary_key=True, autoincrement=False)
    revision = Column(Integer, nullable=False)
    global_revision = Column(Integer, nullable=False)


voice_shared_revision = Table(
    "voice_shared_revision",
    metadata,
    VoiceSharedRevisionColumns,
    CheckConstraint("singleton = 1"),
    CheckConstraint("revision >= 0 AND global_revision >= revision"),
)
