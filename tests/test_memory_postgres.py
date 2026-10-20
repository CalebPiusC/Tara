"""Real PostgreSQL integration for persistent memory (opt-in, marker: db).

Run:
    TARA_TEST_DATABASE_URL=postgresql:///tara_memory_test .venv/bin/python -m pytest -m db tests/test_memory_postgres.py -q
The test creates a throwaway schema and drops it afterwards. It never uses a
production database and never prints the URL.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager

import pytest

from tara.memory import MemoryScope, MemoryService, PostgresMemoryStore, utcnow
from tara.memory.migrate import migrate

pytestmark = pytest.mark.db
run = asyncio.run


def _dsn() -> str:
    pytest.importorskip("psycopg")
    pytest.importorskip("psycopg_pool")
    dsn = os.environ.get("TARA_TEST_DATABASE_URL", "")
    if not dsn:
        pytest.skip("set TARA_TEST_DATABASE_URL to run PostgreSQL memory tests")
    return dsn


@pytest.fixture()
def pg():
    dsn = _dsn()
    schema = f"tara_test_{uuid.uuid4().hex[:10]}"
    migrate(dsn, schema=schema)
    yield dsn, schema
    import psycopg
    from psycopg import sql
    with psycopg.connect(dsn) as conn:
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        conn.commit()


@asynccontextmanager
async def memory(dsn: str, schema: str):
    store = PostgresMemoryStore(dsn, schema=schema)
    try:
        yield store, MemoryService(store)
    finally:
        await store.close()


def scope(user: str = "u1", tenant: str = "t1") -> MemoryScope:
    return MemoryScope(tenant, user)


def test_remember_recall_and_cross_instance_recall(pg):
    dsn, schema = pg

    async def go():
        async with memory(dsn, schema) as (store, service):
            item = await service.remember(scope(), "Testflow is a computer-based testing platform.",
                                          source="s1:turn:2", session_id="s1", kind="project")
            hits = await service.recall(scope(), "What is Testflow?")
            assert [h.item.id for h in hits] == [item.id]

            # A fresh store/service sees the committed row, as a later process would.
            other = PostgresMemoryStore(dsn, schema=schema)
            try:
                later = MemoryService(other)
                hits = await later.recall(scope(), "Testflow")
                assert [h.item.id for h in hits] == [item.id]
            finally:
                await other.close()
        assert store is not None
    run(go())


def test_scope_isolation_and_forget(pg):
    dsn, schema = pg

    async def go():
        async with memory(dsn, schema) as (_, service):
            item = await service.remember(scope("alice"), "CANARY-ALICE private pricing is 42.", source="a:1")
            assert await service.recall(scope("bob"), "CANARY-ALICE private pricing") == []
            assert await service.recall(MemoryScope("t2", "alice"), "CANARY-ALICE private pricing") == []
            assert await service.forget(scope("alice"), item.id) is True
            assert await service.recall(scope("alice"), "CANARY-ALICE") == []
            assert await service.store.get(scope("alice"), item.id) is None
    run(go())


def test_correction_preserves_provenance(pg):
    dsn, schema = pg

    async def go():
        async with memory(dsn, schema) as (store, service):
            old = await service.remember(scope(), "Testflow targets university students.", source="s1:1",
                                         kind="correction")
            new = await service.remember(scope(), "Testflow now targets universities and employers.", source="s1:2",
                                         kind="correction", supersedes_id=old.id)
            hits = await service.recall(scope(), "Who does Testflow target?")
            assert new.id in [h.item.id for h in hits]
            assert old.id not in [h.item.id for h in hits]
            stored_old = await store.get(scope(), old.id)
            assert stored_old is not None and stored_old.status == "superseded"
    run(go())


def test_startup_context_and_concurrent_writes(pg):
    dsn, schema = pg

    async def go():
        async with memory(dsn, schema) as (store, service):
            await asyncio.gather(*[
                service.remember(scope(), f"Project detail {i}: unique value {i}.", source=f"s1:{i}")
                for i in range(12)
            ])
            items = await store.list_recent(scope(), 20)
            assert len(items) == 12
            block = await service.startup_context(scope(), limit=8, max_chars=900)
            assert block.startswith("Long-term memory from earlier conversations")
            assert block.count("- [id=") <= 8
    run(go())


def test_database_failure_is_not_reported_as_a_saved_memory(pg, monkeypatch):
    dsn, schema = pg

    async def go():
        async with memory(dsn, schema) as (store, service):
            async def broken():
                raise RuntimeError("database unavailable")

            monkeypatch.setattr(store, "_ensure", broken)
            with pytest.raises(RuntimeError):
                await service.remember(scope(), "This must not be confirmed.", source="s1:1")
            assert service.metrics.counters["memory_write_failures"] == 1
            block = await service.startup_context(scope())       # degrades to no context, never blocks voice
            assert block == ""
            assert service.metrics.counters["memory_startup_failures"] == 1
    run(go())


def test_expiry_is_enforced_by_the_database(pg):
    dsn, schema = pg

    async def go():
        async with memory(dsn, schema) as (_, service):
            await service.remember(scope(), "Temporary detail.", source="s1:1",
                                   expires_at=utcnow().replace(year=2000))
            assert await service.recall(scope(), "Temporary detail") == []
    run(go())
