"""Process-owned derived voice state; SQLite remains the durable authority."""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial

from api.voice.model import VoiceJournalRecord
from api.voice.timeline import VoiceReplayState, VoiceTimeline
from repositories.voice_repository import VoiceCommit, VoiceHistory, VoiceRepository
from utils.asyncio_utils import run_in_thread

logger = logging.getLogger(__name__)


class AnalyticsUnavailableError(RuntimeError):
    """No current snapshot can be published while recovery is pending."""


@dataclass(frozen=True, slots=True)
class AnalysisSnapshot:
    """Stable analytical view and its committed scoped revision."""

    timeline: VoiceTimeline
    generation: int


class VoiceAnalytics:
    """Serialize runtime commits, replay mutation and stable snapshot publication.

    Restore before starting producers. Journal reloads reuse this owner. A
    failed derived update never changes the outcome of a confirmed SQL write.
    Close after draining the journal and before disposing the shared database.
    Independent writers to the runtime voice tables are not supported.
    """

    def __init__(self, repository: VoiceRepository) -> None:
        self._repository = repository
        self._lock = asyncio.Lock()
        self._state: VoiceReplayState | None = None
        self._cutoff = VoiceCommit(0, 0)
        self._shared_revision = 0
        self._guild_revisions: dict[int, int] = {}
        self._recovery: asyncio.Task[None] | None = None
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Restore once before collection; propagate startup failures."""
        if self._closed:
            raise AnalyticsUnavailableError("Voice analytics is closed")
        if self._state is None:
            await asyncio.shield(self._recover())

    def _recover(self) -> asyncio.Task[None]:
        if self._recovery is None or self._recovery.done():
            self._recovery = asyncio.create_task(self._rebuild(), name="voice-recovery")
            self._recovery.add_done_callback(self._recovery_finished)
        return self._recovery

    @staticmethod
    def _recovery_finished(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.warning("Voice analytics recovery failed; next request may retry")
            logger.debug("Voice recovery traceback", exc_info=task.exception())

    async def _rebuild(self) -> None:
        history = await self._repository.history()
        state = await run_in_thread(partial(VoiceReplayState, history.records))
        async with self._lock:
            # Exclude commits while catching up and publishing one consistent cutoff.
            tail = await self._repository.history(
                after_record_id=history.cutoff.last_record_id
            )
            if not await run_in_thread(partial(state.apply_many, tail.records)):
                history = await self._repository.history()
                state = await run_in_thread(partial(VoiceReplayState, history.records))
            else:
                history = tail
            if not self._closed:
                self._publish(state, history)
                logger.info(
                    "Voice analytics restored: revision=%d record_id=%d",
                    self._cutoff.revision,
                    self._cutoff.last_record_id,
                )

    def _publish(self, state: VoiceReplayState, history: VoiceHistory) -> None:
        self._state = state
        self._cutoff = history.cutoff
        self._shared_revision = history.shared_revision
        self._guild_revisions = history.guild_revisions

    async def append(self, batch_id: str, records: Sequence[VoiceJournalRecord]) -> int:
        """Persist journal work, then update RAM without changing durable success."""
        async with self._lock:
            if self._closed:
                raise AnalyticsUnavailableError("Voice analytics is closed")
            try:
                committed = await self._repository.append_batch(batch_id, records)
            except BaseException:
                # An interrupted commit can have an unknown durable outcome.
                self._state = None
                raise
            await self._apply_committed(committed, records)
            return committed.revision

    async def _apply_committed(
        self, committed: VoiceCommit, records: Sequence[VoiceJournalRecord]
    ) -> None:
        if committed.revision <= self._cutoff.revision:
            return
        state, self._state = self._state, None
        try:
            if (
                state is not None
                and committed.revision == self._cutoff.revision + 1
                and await run_in_thread(partial(state.apply_many, records))
            ):
                self._cutoff = committed
                for record in records:
                    if record.guild_id is None:
                        self._shared_revision = committed.revision
                    else:
                        self._guild_revisions[record.guild_id] = committed.revision
                self._state = state
        except Exception:
            logger.warning(
                "Voice facts committed but analytics needs recovery: revision=%d",
                committed.revision,
            )
            logger.debug("Voice apply traceback", exc_info=True)
        finally:
            if self._state is None and not self._closed:
                self._recover()

    async def snapshot(self, guild_id: int | None = None) -> AnalysisSnapshot:
        """Read RAM only; reject dirty state and trigger one owned recovery."""
        async with self._lock:
            if self._closed:
                raise AnalyticsUnavailableError("Voice analytics is closed")
            if self._state is None:
                self._recover()
                raise AnalyticsUnavailableError("Voice analytics is recovering")
            timeline = await run_in_thread(partial(self._state.snapshot, guild_id))
            revision = (
                self._cutoff.revision
                if guild_id is None
                else max(self._shared_revision, self._guild_revisions.get(guild_id, 0))
            )
            return AnalysisSnapshot(timeline, revision)

    async def close(self) -> None:
        """Close admission and join workers, including cancelled startup recovery."""
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._drain())
        await asyncio.shield(self._close_task)

    async def _drain(self) -> None:
        if self._recovery is not None:
            await asyncio.gather(self._recovery, return_exceptions=True)
        async with self._lock:
            self._state = None
