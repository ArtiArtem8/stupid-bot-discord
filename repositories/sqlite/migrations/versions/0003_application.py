"""Normalize identities and migrate the two existing feature stores."""

from alembic import op
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)

from repositories.sqlite.migrations.normalize_0002 import transfer_existing

revision = "0003_application"
down_revision = "0002_music_volume"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create shared identities and durable feature tables, preserving existing data."""
    op.create_table(
        "users",
        Column("user_id", Integer(), primary_key=True, autoincrement=False),
        Column("username", String(), nullable=True),
        Column("global_name", String(), nullable=True),
        Column("is_bot", Boolean(), nullable=True),
        Column("observed_us", Integer(), nullable=True),
        CheckConstraint("user_id > 0"),
        CheckConstraint("is_bot IS NULL OR is_bot IN (0, 1)"),
    )
    op.create_table(
        "guilds",
        Column("guild_id", Integer(), primary_key=True, autoincrement=False),
        Column("name", String(), nullable=True),
        Column("observed_us", Integer(), nullable=True),
        CheckConstraint("guild_id > 0"),
    )
    op.create_table(
        "guild_members",
        Column("guild_id", Integer(), primary_key=True, autoincrement=False),
        Column("user_id", Integer(), primary_key=True, autoincrement=False),
        Column("nickname", String(), nullable=True),
        Column("observed_us", Integer(), nullable=True),
        ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"]),
        ForeignKeyConstraint(["user_id"], ["users.user_id"]),
    )
    op.create_index("ix_members_user_guild", "guild_members", ["user_id", "guild_id"])
    op.create_table(
        "channels",
        Column("channel_id", Integer(), primary_key=True, autoincrement=False),
        Column("guild_id", Integer(), nullable=True),
        Column("name", String(), nullable=True),
        Column("kind", String(), nullable=True),
        Column("observed_us", Integer(), nullable=True),
        CheckConstraint("channel_id > 0"),
        ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"]),
        UniqueConstraint("channel_id", "guild_id"),
    )
    op.create_table(
        "roles",
        Column("role_id", Integer(), primary_key=True, autoincrement=False),
        Column("guild_id", Integer(), nullable=False),
        Column("name", String(), nullable=True),
        Column("deleted_us", Integer(), nullable=True),
        CheckConstraint("role_id > 0"),
        ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"]),
        UniqueConstraint("role_id", "guild_id"),
    )
    op.create_index("ix_roles_guild", "roles", ["guild_id"])
    op.create_table(
        "storage_state",
        Column("singleton", Integer(), primary_key=True, autoincrement=False),
        Column("status", String(), nullable=False),
        Column("manifest_hash", String(), nullable=True),
        Column("origin", String(), nullable=False),
        CheckConstraint("singleton = 1"),
        CheckConstraint("status IN ('BUILDING','COMPLETE')"),
    )
    op.create_table(
        "migration_sources",
        Column("source_name", String(), primary_key=True),
        Column("digest", String(), nullable=False),
        Column("record_count", Integer(), nullable=False),
        Column("schema_version", String(), nullable=False),
        Column("bounds_hint", String(), nullable=True),
        CheckConstraint("record_count >= 0"),
    )
    op.create_table(
        "birthday_settings",
        Column("guild_id", Integer(), primary_key=True, autoincrement=False),
        Column("channel_id", Integer(), nullable=True),
        Column("birthday_role_id", Integer(), nullable=True),
        Column("guild_name_hint", String(), nullable=False),
        Column("version", Integer(), nullable=False),
        ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"]),
        ForeignKeyConstraint(
            ["channel_id", "guild_id"], ["channels.channel_id", "channels.guild_id"]
        ),
        ForeignKeyConstraint(
            ["birthday_role_id", "guild_id"], ["roles.role_id", "roles.guild_id"]
        ),
        CheckConstraint("version > 0"),
    )
    op.create_table(
        "member_birthdays",
        Column("guild_id", Integer(), primary_key=True, autoincrement=False),
        Column("user_id", Integer(), primary_key=True, autoincrement=False),
        Column("birth_date", String(), nullable=True),
        Column("position", Integer(), nullable=False),
        Column("display_name_hint", String(), nullable=False),
        Column("version", Integer(), nullable=False),
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
    op.create_index(
        "ix_birthdays_user_guild", "member_birthdays", ["user_id", "guild_id"]
    )
    op.create_table(
        "birthday_history",
        Column("guild_id", Integer(), primary_key=True, autoincrement=False),
        Column("user_id", Integer(), primary_key=True, autoincrement=False),
        Column("position", Integer(), primary_key=True, autoincrement=False),
        Column("value", String(), nullable=False),
        Column("origin", String(), nullable=False),
        ForeignKeyConstraint(
            ["guild_id", "user_id"],
            ["member_birthdays.guild_id", "member_birthdays.user_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("position >= 0"),
    )
    op.create_table(
        "birthday_deliveries",
        Column("guild_id", Integer(), primary_key=True, autoincrement=False),
        Column("user_id", Integer(), primary_key=True, autoincrement=False),
        Column("calendar_date", String(), primary_key=True),
        Column("operation_id", String(), nullable=False),
        Column("settings_version", Integer(), nullable=False),
        Column("birthday_version", Integer(), nullable=False),
        Column("status", String(), nullable=False),
        Column("message_id", Integer(), nullable=True),
        Column("updated_us", Integer(), nullable=False),
        ForeignKeyConstraint(
            ["guild_id", "user_id"], ["guild_members.guild_id", "guild_members.user_id"]
        ),
        UniqueConstraint("operation_id"),
        CheckConstraint("status IN ('claimed','sent','uncertain','obsolete')"),
        CheckConstraint("message_id IS NULL OR message_id > 0"),
    )
    op.create_table(
        "music_settings",
        Column("guild_id", Integer(), primary_key=True, autoincrement=False),
        Column("volume", Integer(), nullable=False),
        Column("version", Integer(), nullable=False),
        ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"]),
        CheckConstraint("volume >= 0 AND volume <= 200"),
        CheckConstraint("version > 0"),
    )
    op.create_table(
        "member_blocks",
        Column("guild_id", Integer(), primary_key=True, autoincrement=False),
        Column("user_id", Integer(), primary_key=True, autoincrement=False),
        Column("blocked", Boolean(), nullable=False),
        Column("display_name_hint", String(), nullable=False),
        Column("username_hint", String(), nullable=True),
        Column("version", Integer(), nullable=False),
        ForeignKeyConstraint(
            ["guild_id", "user_id"], ["guild_members.guild_id", "guild_members.user_id"]
        ),
        CheckConstraint("version > 0"),
        CheckConstraint("blocked IN (0, 1)"),
    )
    op.create_table(
        "block_events",
        Column("event_id", Integer(), primary_key=True, autoincrement=True),
        Column("guild_id", Integer(), nullable=False),
        Column("user_id", Integer(), nullable=False),
        Column("action", String(), nullable=False),
        Column("ordinal", Integer(), nullable=False),
        Column("admin_id", Integer(), nullable=False),
        Column("reason", String(), nullable=True),
        Column("created_us", Integer(), nullable=False),
        Column("origin", String(), nullable=False),
        ForeignKeyConstraint(
            ["guild_id", "user_id"], ["guild_members.guild_id", "guild_members.user_id"]
        ),
        ForeignKeyConstraint(["admin_id"], ["users.user_id"]),
        UniqueConstraint("guild_id", "user_id", "action", "ordinal"),
        CheckConstraint("action IN ('block','unblock')"),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "ix_block_events_member", "block_events", ["guild_id", "user_id", "event_id"]
    )
    op.create_index("ix_block_events_admin", "block_events", ["admin_id"])
    op.create_table(
        "member_name_observations",
        Column("guild_id", Integer(), primary_key=True, autoincrement=False),
        Column("user_id", Integer(), primary_key=True, autoincrement=False),
        Column("ordinal", Integer(), primary_key=True, autoincrement=False),
        Column("display_name", String(), nullable=False),
        Column("created_us", Integer(), nullable=False),
        ForeignKeyConstraint(
            ["guild_id", "user_id"], ["guild_members.guild_id", "guild_members.user_id"]
        ),
    )
    op.create_table(
        "report_settings",
        Column("singleton", Integer(), primary_key=True, autoincrement=False),
        Column("channel_id", Integer(), nullable=True),
        Column("version", Integer(), nullable=False),
        CheckConstraint("singleton = 1"),
        ForeignKeyConstraint(["channel_id"], ["channels.channel_id"]),
        CheckConstraint("version > 0"),
    )
    op.create_table(
        "reports",
        Column("report_id", String(), primary_key=True),
        Column("position", Integer(), nullable=False, unique=True),
        Column("request_key", String(), nullable=True),
        Column("user_id", Integer(), nullable=False),
        Column("guild_id", Integer(), nullable=True),
        Column("channel_id", Integer(), nullable=True),
        Column("reason", String(), nullable=False),
        Column("created_us", Integer(), nullable=True),
        Column("created_text", String(), nullable=True),
        Column("user_name_at_event", String(), nullable=False),
        Column("avatar_at_event", String(), nullable=True),
        Column("guild_name_at_event", String(), nullable=True),
        Column("channel_name_at_event", String(), nullable=True),
        Column("provenance", String(), nullable=False),
        ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"]),
        ForeignKeyConstraint(["channel_id"], ["channels.channel_id"]),
        UniqueConstraint("request_key"),
        CheckConstraint("position >= 0"),
    )
    op.create_index("ix_reports_user", "reports", ["user_id", "report_id"])
    op.create_index("ix_reports_guild", "reports", ["guild_id", "report_id"])
    op.create_index("ix_reports_channel", "reports", ["channel_id"])
    op.create_table(
        "question_answers",
        Column("user_id", Integer(), primary_key=True, autoincrement=False),
        Column("normalized_question", String(), primary_key=True),
        Column("answer", String(), nullable=False),
        ForeignKeyConstraint(["user_id"], ["users.user_id"]),
    )
    op.create_table(
        "monitor_settings",
        Column("guild_id", Integer(), primary_key=True, autoincrement=False),
        Column("enabled", Boolean(), nullable=False),
        Column("ttl_days", Integer(), nullable=True),
        Column("version", Integer(), nullable=False),
        ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"]),
        CheckConstraint("ttl_days IS NULL OR ttl_days > 0"),
        CheckConstraint("version > 0"),
        CheckConstraint("enabled IN (0, 1)"),
    )
    op.create_table(
        "role_snapshots",
        Column("snapshot_id", Integer(), primary_key=True, autoincrement=True),
        Column("guild_id", Integer(), nullable=False),
        Column("user_id", Integer(), nullable=False),
        Column("left_us", Integer(), nullable=False),
        Column("username_at_leave", String(), nullable=False),
        Column("version", Integer(), nullable=False),
        ForeignKeyConstraint(
            ["guild_id", "user_id"], ["guild_members.guild_id", "guild_members.user_id"]
        ),
        UniqueConstraint("guild_id", "user_id"),
        UniqueConstraint("snapshot_id", "guild_id"),
        CheckConstraint("version > 0"),
        sqlite_autoincrement=True,
    )
    op.create_table(
        "role_snapshot_roles",
        Column("snapshot_id", Integer(), primary_key=True, autoincrement=False),
        Column("guild_id", Integer(), nullable=False),
        Column("role_id", Integer(), nullable=False),
        Column("position", Integer(), primary_key=True, autoincrement=False),
        ForeignKeyConstraint(
            ["snapshot_id", "guild_id"],
            ["role_snapshots.snapshot_id", "role_snapshots.guild_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["role_id", "guild_id"], ["roles.role_id", "roles.guild_id"]
        ),
    )
    op.create_index(
        "ix_snapshot_roles_role", "role_snapshot_roles", ["role_id", "guild_id"]
    )
    op.create_table(
        "uptime_periods",
        Column("period_id", Integer(), primary_key=True, autoincrement=True),
        Column("started_us", Integer(), nullable=True),
        Column("accumulated_us", Integer(), nullable=False),
        Column("last_checkpoint_us", Integer(), nullable=False),
        Column("archived_us", Integer(), nullable=True),
        Column("reset_reason", String(), nullable=True),
        Column("origin", String(), nullable=False),
        CheckConstraint("accumulated_us >= 0"),
        CheckConstraint("(archived_us IS NULL) = (reset_reason IS NULL)"),
        sqlite_autoincrement=True,
    )
    op.create_table(
        "runtime_checkpoint",
        Column("singleton", Integer(), primary_key=True, autoincrement=False),
        Column("checkpoint_us", Integer(), nullable=False),
        Column("accumulated_us", Integer(), nullable=False),
        Column("origin", String(), nullable=False),
        Column("boot_id", String(), nullable=True),
        Column("period_id", Integer(), nullable=False),
        ForeignKeyConstraint(["period_id"], ["uptime_periods.period_id"]),
        CheckConstraint("singleton = 1"),
        CheckConstraint("accumulated_us >= 0"),
        CheckConstraint(
            "origin IN ('legacy_checkpoint','startup','autosave','shutdown')"
        ),
    )
    op.create_table(
        "voice_batches",
        Column("batch_id", String(), primary_key=True),
        Column("fingerprint", String(), nullable=False),
        Column("record_count", Integer(), nullable=False),
        Column("revision", Integer(), nullable=False),
        CheckConstraint("record_count >= 0"),
        CheckConstraint("revision >= 0"),
    )
    op.create_table(
        "voice_records",
        Column("record_id", Integer(), primary_key=True, autoincrement=True),
        Column("batch_id", String(), nullable=False),
        Column("ordinal", Integer(), nullable=False),
        Column("boot_id", String(), nullable=False),
        Column("sequence", Integer(), nullable=False),
        Column("observed_us", Integer(), nullable=False),
        Column("monotonic", Float(), nullable=False),
        Column("kind", String(), nullable=False),
        Column("guild_id", Integer(), nullable=True),
        Column("authoritative", Boolean(), nullable=True),
        Column("stopped", Boolean(), nullable=True),
        Column("gap_start_us", Integer(), nullable=True),
        Column("gap_end_us", Integer(), nullable=True),
        Column("gap_reason", String(), nullable=True),
        Column("known_bounds", Boolean(), nullable=True),
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
            "(kind = 'gap') = (gap_start_us IS NOT NULL AND gap_reason "
            + "IS NOT NULL AND known_bounds IS NOT NULL)"
        ),
        CheckConstraint(
            "kind NOT IN ('observation','snapshot') OR guild_id IS NOT NULL"
        ),
        CheckConstraint("gap_end_us IS NULL OR gap_end_us >= gap_start_us"),
        CheckConstraint("authoritative IS NULL OR authoritative IN (0, 1)"),
        CheckConstraint("stopped IS NULL OR stopped IN (0, 1)"),
        CheckConstraint("known_bounds IS NULL OR known_bounds IN (0, 1)"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_voice_scope_cursor", "voice_records", ["guild_id", "record_id"])
    op.create_index(
        "ix_voice_replay", "voice_records", ["boot_id", "sequence", "kind", "record_id"]
    )
    op.create_table(
        "voice_record_states",
        Column("record_id", Integer(), primary_key=True, autoincrement=False),
        Column("position", Integer(), primary_key=True, autoincrement=False),
        Column("guild_id", Integer(), nullable=False),
        Column("user_id", Integer(), nullable=False),
        Column("channel_id", Integer(), nullable=True),
        Column("channel_known", Boolean(), nullable=False),
        Column("is_bot", Boolean(), nullable=True),
        Column("self_mute", Boolean(), nullable=True),
        Column("self_deaf", Boolean(), nullable=True),
        Column("server_mute", Boolean(), nullable=True),
        Column("server_deaf", Boolean(), nullable=True),
        Column("self_stream", Boolean(), nullable=True),
        Column("self_video", Boolean(), nullable=True),
        Column("suppress", Boolean(), nullable=True),
        Column("afk", Boolean(), nullable=True),
        Column("requested_to_speak", Boolean(), nullable=True),
        Column("requested_us", Integer(), nullable=True),
        Column("session_id", String(), nullable=True),
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
    op.create_index(
        "ix_voice_state_user_record", "voice_record_states", ["user_id", "record_id"]
    )
    op.create_index(
        "ix_voice_state_member", "voice_record_states", ["guild_id", "user_id"]
    )
    op.create_index(
        "ix_voice_state_channel", "voice_record_states", ["channel_id", "guild_id"]
    )
    op.create_table(
        "voice_revisions",
        Column("guild_id", Integer(), primary_key=True, autoincrement=False),
        Column("revision", Integer(), nullable=False),
        ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"]),
        CheckConstraint("revision >= 0"),
    )
    op.create_table(
        "voice_shared_revision",
        Column("singleton", Integer(), primary_key=True, autoincrement=False),
        Column("revision", Integer(), nullable=False),
        Column("global_revision", Integer(), nullable=False),
        CheckConstraint("singleton = 1"),
        CheckConstraint("revision >= 0 AND global_revision >= revision"),
    )
    transfer_existing(op.get_bind())


def downgrade() -> None:
    """Require restoration of a pre-migration backup instead of lossy downgrade."""
    raise RuntimeError("Restore a backup with the previous application version")
