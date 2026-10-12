"""Add optional presentation overrides and independent reminder reservations."""

from alembic import op
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    Integer,
    String,
)

revision = "0004_preferences"
down_revision = "0003_application"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Preserve existing settings; absence of an override has no install-age meaning."""
    for scope, identity in (("user", "users.user_id"), ("guild", "guilds.guild_id")):
        op.create_table(
            f"{scope}_preferences",
            Column("subject_id", Integer(), primary_key=True, autoincrement=False),
            Column("language", String(), nullable=True),
            Column("timezone", String(), nullable=True),
            Column("revision", Integer(), nullable=False),
            ForeignKeyConstraint(["subject_id"], [identity]),
            CheckConstraint("language IS NULL OR language IN ('ru', 'en')"),
            CheckConstraint("revision > 0"),
        )
    op.create_table(
        "preference_reminders",
        Column("user_id", Integer(), primary_key=True, autoincrement=False),
        Column("eligible_uses", Integer(), nullable=False),
        Column("prompt_count", Integer(), nullable=False),
        Column("last_prompt_us", Integer(), nullable=True),
        Column("dismissed", Boolean(), nullable=False),
        Column("token", String(), nullable=True),
        ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        CheckConstraint("eligible_uses >= 0"),
        CheckConstraint("prompt_count BETWEEN 0 AND 2"),
        CheckConstraint("dismissed IN (0, 1)"),
    )


def downgrade() -> None:
    """Require an explicit restore instead of silently discarding user settings."""
    raise NotImplementedError("Restore a backup to downgrade preferences")
