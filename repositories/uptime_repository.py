"""Persist uptime checkpoints and retain periods across downtime-triggered resets."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from repositories.sqlite.database import Database
from repositories.sqlite.schema import runtime_checkpoint, uptime_periods


@dataclass(frozen=True, slots=True)
class UptimeCheckpoint:
    """One confirmed checkpoint; a checkpoint is not proof of a clean shutdown."""

    period_id: int
    checkpoint_us: int
    accumulated_us: int


class UptimeRepository:
    """Own reset/archive atomicity and reject checkpoints from obsolete processes."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def restore_or_start(
        self, *, now_us: int, threshold_us: int, boot_id: str
    ) -> UptimeCheckpoint:
        """Resume a short outage or archive the old period before starting a new one.

        Archive time is when this process observes the reset. The old period's
        last checkpoint retains its actual observation time; no crash time is
        invented. Imported periods may have an unknown start.
        """
        if threshold_us <= 0 or not boot_id:
            raise ValueError(
                "Uptime requires a positive threshold and process identity"
            )
        async with self._database.transaction() as connection:
            previous = (
                await connection.execute(
                    select(
                        runtime_checkpoint.c.period_id,
                        runtime_checkpoint.c.checkpoint_us,
                        runtime_checkpoint.c.accumulated_us,
                    ).where(runtime_checkpoint.c.singleton == 1)
                )
            ).one_or_none()
            accumulated = 0
            if previous is not None and now_us - previous[1] < threshold_us:
                period_id, _, accumulated = previous
                await connection.execute(
                    uptime_periods.update()
                    .where(uptime_periods.c.period_id == period_id)
                    .values(last_checkpoint_us=now_us)
                )
            else:
                if previous is not None:
                    await connection.execute(
                        uptime_periods.update()
                        .where(uptime_periods.c.period_id == previous[0])
                        .values(archived_us=now_us, reset_reason="offline_threshold")
                    )
                period_id = (
                    await connection.execute(
                        insert(uptime_periods)
                        .values(
                            started_us=now_us,
                            accumulated_us=0,
                            last_checkpoint_us=now_us,
                            origin="observed",
                        )
                        .returning(uptime_periods.c.period_id)
                    )
                ).scalar_one()
            statement = insert(runtime_checkpoint).values(
                singleton=1,
                period_id=period_id,
                checkpoint_us=now_us,
                accumulated_us=accumulated,
                origin="startup",
                boot_id=boot_id,
            )
            await connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[runtime_checkpoint.c.singleton],
                    set_={
                        "period_id": period_id,
                        "checkpoint_us": now_us,
                        "accumulated_us": accumulated,
                        "origin": "startup",
                        "boot_id": boot_id,
                    },
                )
            )
            return UptimeCheckpoint(period_id, now_us, accumulated)

    async def save(
        self,
        checkpoint: UptimeCheckpoint,
        *,
        boot_id: str,
        final: bool = False,
    ) -> None:
        """Commit the current checkpoint and its period total in one transaction."""
        if checkpoint.accumulated_us < 0:
            raise ValueError("Uptime cannot be negative")
        async with self._database.transaction() as connection:
            saved = await connection.scalar(
                runtime_checkpoint.update()
                .where(
                    runtime_checkpoint.c.singleton == 1,
                    runtime_checkpoint.c.period_id == checkpoint.period_id,
                    runtime_checkpoint.c.boot_id == boot_id,
                )
                .values(
                    checkpoint_us=checkpoint.checkpoint_us,
                    accumulated_us=checkpoint.accumulated_us,
                    origin="shutdown" if final else "autosave",
                )
                .returning(runtime_checkpoint.c.period_id)
            )
            if saved is None:
                raise RuntimeError("Uptime checkpoint belongs to an obsolete process")
            await connection.execute(
                uptime_periods.update()
                .where(uptime_periods.c.period_id == checkpoint.period_id)
                .values(
                    last_checkpoint_us=checkpoint.checkpoint_us,
                    accumulated_us=checkpoint.accumulated_us,
                )
            )
