"""Own globally user-scoped normalized question answers."""

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from repositories.sqlite.database import Database
from repositories.sqlite.identity import ensure_user
from repositories.sqlite.schema import question_answers


class QuestionRepository:
    """Commit one answer per user/question; return the winner of concurrent inserts."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def answer(
        self, user_id: int, normalized_question: str, proposed: str
    ) -> tuple[str, bool]:
        """Return (committed answer, inserted); normalization belongs to the caller."""
        async with self._database.transaction() as connection:
            await ensure_user(connection, user_id)
            inserted = await connection.scalar(
                insert(question_answers)
                .values(
                    user_id=user_id,
                    normalized_question=normalized_question,
                    answer=proposed,
                )
                .on_conflict_do_nothing()
                .returning(question_answers.c.answer)
            )
            if inserted is not None:
                return inserted, True
            existing = (
                await connection.execute(
                    select(question_answers.c.answer).where(
                        question_answers.c.user_id == user_id,
                        question_answers.c.normalized_question == normalized_question,
                    )
                )
            ).scalar_one()
            return existing, False
