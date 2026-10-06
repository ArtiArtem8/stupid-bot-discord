"""Create birthday guilds and members while preserving legacy history values."""

import sqlalchemy as sa
from alembic import op

revision = "0001_birthdays"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the initial pilot schema without importing mutable application tables."""
    op.create_table(
        "birthday_guilds",
        sa.Column("guild_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("server_name", sa.String(), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("birthday_role_id", sa.Integer(), nullable=True),
    )
    op.create_table(
        "birthday_users",
        sa.Column(
            "guild_id",
            sa.Integer(),
            sa.ForeignKey("birthday_guilds.guild_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("user_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("birthday", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
    )
    op.create_table(
        "birthday_congratulations",
        sa.Column("guild_id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("value", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["guild_id", "user_id"],
            ["birthday_users.guild_id", "birthday_users.user_id"],
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    """Remove the pilot tables in foreign-key order."""
    op.drop_table("birthday_congratulations")
    op.drop_table("birthday_users")
    op.drop_table("birthday_guilds")
