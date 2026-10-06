"""Add independent guild volume settings without modifying birthday tables."""

import sqlalchemy as sa
from alembic import op

revision = "0002_music_volume"
down_revision = "0001_birthdays"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create volume settings even for guilds without a birthday registration."""
    op.create_table(
        "music_volumes",
        sa.Column("guild_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("volume", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    """Drop volume settings while preserving birthday data."""
    op.drop_table("music_volumes")
