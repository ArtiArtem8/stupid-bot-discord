"""Run migrations only through the explicit maintenance entry point."""

from alembic import context
from sqlalchemy import Connection

from stupidbot.storage.schema import metadata

connection: object = context.config.attributes.get("connection")
if not isinstance(connection, Connection):
    raise RuntimeError("Run migrations through python -m stupidbot.storage")
context.configure(connection=connection, target_metadata=metadata)
with context.begin_transaction():
    context.run_migrations()
