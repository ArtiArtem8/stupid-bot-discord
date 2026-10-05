"""Run migrations only through the pilot's explicit maintenance entry point."""

from alembic import context
from sqlalchemy import Connection

from experiments.birthday_sqlite.schema import metadata

connection: object = context.config.attributes.get("connection")
if not isinstance(connection, Connection):
    raise RuntimeError("Run migrations through python -m experiments.birthday_sqlite")
context.configure(connection=connection, target_metadata=metadata)
with context.begin_transaction():
    context.run_migrations()
