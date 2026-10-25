"""Two ways the Farm reported the wrong thing (owner's account check, 2026-10-25).

* codex-01 answered with a NUL byte in its output; PostgreSQL text and jsonb reject NUL, so the bookkeeping write
  failed and a finished job was recorded as a crash. Every pool connection now drops NUL on the way in.
* agy-01, agy-02 and agy-07 exited cleanly with an empty answer to the test prompt and were reported "OK". An empty
  answer is a failure.
"""

from __future__ import annotations

from datetime import UTC, datetime

import psycopg
from psycopg.types.json import Jsonb

from farm.ai.accounts import recheck_login
from farm.db.pool import DbPool, open_pool, run
from farm.executors.base import ExecRequest, ExecResult


def test_text_and_json_with_a_nul_byte_are_stored_without_it(db_url: str) -> None:
    async def main() -> tuple[str, object]:
        pool: DbPool = await open_pool(db_url)
        try:
            async with pool.connection() as conn:
                cur = await conn.execute("select %s::text, %s::jsonb", ("PO\x00NG", Jsonb({"text": "a\x00b"})))
                row = await cur.fetchone()
                assert row is not None
                return row[0], row[1]
        finally:
            await pool.close()

    text, data = run(main())
    assert text == "PONG"
    assert data == {"text": "ab"}


class _Silent:
    """A CLI that exits cleanly and says nothing."""

    async def execute(self, req: ExecRequest) -> ExecResult:
        return ExecResult(ok=True, data={"text": "  "})


def test_an_empty_answer_to_the_test_prompt_is_a_failure(db_url: str) -> None:
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute(
            "insert into public.providers (id, name, kind, executor) values ('gemini', 'Gemini', 'ai', 'cli_agent')"
            " on conflict do nothing"
        )
        conn.execute(
            "insert into public.connections (id, provider_id, auth_ref, meta, status) values (%s, %s, %s, %s, %s)",
            ("agy-09", "gemini", "cli:agy-09", Jsonb({"cli": "agy"}), "needs_login"),
        )

    async def main() -> ExecResult:
        pool: DbPool = await open_pool(db_url)
        try:
            return await recheck_login(pool, _Silent(), "agy-09", now=datetime.now(UTC))
        finally:
            await pool.close()

    result = run(main())
    assert not result.ok and result.error == "empty answer to the test prompt"
    with psycopg.connect(db_url) as conn:
        status = conn.execute("select status from public.connections where id = 'agy-09'").fetchone()
    assert status == ("needs_login",)  # an empty answer never brings an account back
