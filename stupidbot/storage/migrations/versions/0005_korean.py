"""Allow Korean overrides without changing saved preferences or reminders."""

from alembic import op
from sqlalchemy import CheckConstraint, Column, Integer, MetaData, String, Table

revision = "0005_korean"
down_revision = "0004_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Rebuild the two leaf tables with the expanded language constraint."""
    for scope, identity in (("user", "users.user_id"), ("guild", "guilds.guild_id")):
        # Supply the full leaf-table shape: SQLite cannot alter an unnamed CHECK.
        # Foreign keys are recreated explicitly after the existing rows are copied.
        table = Table(
            f"{scope}_preferences",
            MetaData(),
            Column("subject_id", Integer(), primary_key=True, autoincrement=False),
            Column("language", String(), nullable=True),
            Column("timezone", String(), nullable=True),
            Column("revision", Integer(), nullable=False),
            CheckConstraint("language IS NULL OR language IN ('ru', 'en', 'ko')"),
            CheckConstraint("revision > 0"),
        )
        target, column = identity.split(".")
        with op.batch_alter_table(
            table.name, copy_from=table, recreate="always"
        ) as batch:
            batch.create_foreign_key(
                f"fk_{scope}_preferences_subject", target, ["subject_id"], [column]
            )


def downgrade() -> None:
    """Require a restore rather than discarding Korean preferences."""
    raise NotImplementedError("Restore a backup to downgrade Korean preferences")
