"""Real PostgreSQL integration for document RAG (opt-in, marker: db).

Run:
    TARA_TEST_DATABASE_URL=postgresql:///postgres .venv/bin/python -m pytest -m db tests/memory/test_knowledge_postgres.py -q
Each test creates a throwaway schema and drops it afterwards.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager

import pytest

from tara.memory import KnowledgeService, MemoryScope, PostgresKnowledgeStore
from tara.memory.migrate import migrate

pytestmark = pytest.mark.db
run = asyncio.run


def _dsn() -> str:
    pytest.importorskip("psycopg")
    pytest.importorskip("psycopg_pool")
    dsn = os.environ.get("TARA_TEST_DATABASE_URL", "")
    if not dsn:
        pytest.skip("set TARA_TEST_DATABASE_URL to run PostgreSQL knowledge tests")
    return dsn


@pytest.fixture()
def pg():
    dsn = _dsn()
    schema = f"tara_knowledge_{uuid.uuid4().hex[:10]}"
    migrate(dsn, schema=schema)
    yield dsn, schema
    import psycopg
    from psycopg import sql
    with psycopg.connect(dsn) as conn:
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        conn.commit()


@asynccontextmanager
async def knowledge(dsn: str, schema: str):
    store = PostgresKnowledgeStore(dsn, schema=schema)
    try:
        yield store, KnowledgeService(store)
    finally:
        await store.close()


def scope(user: str = "u1", tenant: str = "t1", section: str | None = None) -> MemoryScope:
    return MemoryScope(tenant, user, meeting_id=section)


async def ingest(svc: KnowledgeService, text: str, *, source: str = "handbook.md", title: str = "Handbook",
                 user: str = "u1", tenant: str = "t1", section: str | None = None, approved: bool = True):
    return await svc.ingest_text(scope(user, tenant, section), title, text, source=source,
                                 section_id=section, approved=approved)


def test_ingest_search_and_a_new_store_reads_the_same_document(pg):
    dsn, schema = pg

    async def go():
        async with knowledge(dsn, schema) as (_, svc):
            document = await ingest(svc, "# Refund\n\nRefunds take 30 days.", title="Refund Policy",
                                    source="refund.md")
            hits = await svc.search(scope(), "how long do refunds take")
            assert [h.document.id for h in hits] == [document.id]
            assert "30 days" in hits[0].chunk.text and "v1" in hits[0].citation

        async with knowledge(dsn, schema) as (_, later):
            hits = await later.search(scope(), "refund policy")
            assert hits and hits[0].document.id == document.id
            listed = await later.list_documents(scope())
            assert [d.id for d in listed] == [document.id]
    run(go())


def test_acls_are_applied_before_content_and_cannot_cross_tenants(pg):
    dsn, schema = pg

    async def go():
        async with knowledge(dsn, schema) as (_, svc):
            document = await ingest(svc, "CANARY-SHARED refund window is 30 days.", source="shared.md", user="alice")
            alice, bob = scope("alice"), scope("bob")
            assert await svc.search(bob, "CANARY-SHARED") == []
            assert await svc.search(scope("bob", "t2"), "CANARY-SHARED") == []
            assert await svc.get(bob, document.id) is None

            await svc.grant(alice, document.id, "bob")
            assert (await svc.search(bob, "CANARY-SHARED"))[0].document.id == document.id
            assert await svc.search(scope("bob", "t2"), "CANARY-SHARED") == []

            await svc.revoke(alice, document.id, "bob")
            assert await svc.search(bob, "CANARY-SHARED") == []
    run(go())


def test_section_scoped_documents_stay_in_their_section(pg):
    dsn, schema = pg

    async def go():
        async with knowledge(dsn, schema) as (_, svc):
            global_doc = await ingest(svc, "All staff get 25 days of leave.", source="global.md")
            section_doc = await ingest(svc, "Onboarding leave is 20 days.", source="onboarding.md",
                                       section="onboarding")
            in_onboarding = await svc.search(scope(section="onboarding"), "leave")
            assert {h.document.id for h in in_onboarding} == {global_doc.id, section_doc.id}
            in_sales = await svc.search(scope(section="sales"), "leave")
            assert {h.document.id for h in in_sales} == {global_doc.id}
            everywhere = await svc.search(scope(), "leave")
            assert {h.document.id for h in everywhere} == {global_doc.id, section_doc.id}
    run(go())


def test_versions_are_immutable_and_current_version_only_is_searchable(pg):
    dsn, schema = pg

    async def go():
        async with knowledge(dsn, schema) as (_, svc):
            first = await ingest(svc, "The refund window is 30 days.", source="refund.md")
            second = await ingest(svc, "The refund window is 14 days.", source="refund.md")
            assert second.id == first.id and second.version == 2
            current = await svc.search(scope(), "refund window")
            assert current and all(h.document.version == 2 for h in current)
            assert "30 days" not in " ".join(h.chunk.text for h in current)

            same = await ingest(svc, "The refund window is 14 days.", source="refund.md")
            assert same.version == 2

            reverted = await ingest(svc, "The refund window is 30 days.", source="refund.md")
            assert reverted.version == 1
            reverted_hits = await svc.search(scope(), "30 days")
            assert reverted_hits and reverted_hits[0].document.version == 1
            assert "30 days" in reverted_hits[0].chunk.text
    run(go())


def test_approval_drafts_and_shared_drafts(pg):
    dsn, schema = pg

    async def go():
        async with knowledge(dsn, schema) as (_, svc):
            document = await ingest(svc, "DRAFT-CANARY refund window is 12 days.", source="draft.md",
                                    user="alice", approved=False)
            await svc.grant(scope("alice"), document.id, "bob")
            assert await svc.search(scope("alice"), "DRAFT-CANARY") == []
            assert await svc.search(scope("bob"), "DRAFT-CANARY") == []
            assert await svc.list_documents(scope("alice")) == []
            assert [d.id for d in await svc.list_documents(scope("alice"), include_unapproved=True)] == [document.id]

            await svc.set_approved(scope("alice"), document.id, True)
            assert (await svc.search(scope("bob"), "DRAFT-CANARY"))[0].document.id == document.id
            await svc.set_approved(scope("alice"), document.id, False)
            assert await svc.search(scope("bob"), "DRAFT-CANARY") == []
    run(go())


def test_soft_delete_purge_and_reingest(pg):
    dsn, schema = pg

    async def go():
        async with knowledge(dsn, schema) as (_, svc):
            document = await ingest(svc, "Retention is seven years.", source="retention.md")
            assert await svc.delete(scope(), document.id) is True
            assert await svc.search(scope(), "retention") == []
            assert await svc.get(scope(), document.id) is None
            assert await svc.purge_deleted(scope()) == 1
            assert await svc.purge_deleted(scope()) == 0

            replacement = await ingest(svc, "Retention is seven years.", source="retention.md")
            assert replacement.id != document.id
            assert await svc.delete(scope(), replacement.id, hard=True) is True
            assert await svc.get(scope(), replacement.id) is None
    run(go())


def test_same_source_is_one_family_per_owner_and_section(pg):
    dsn, schema = pg

    async def go():
        async with knowledge(dsn, schema) as (_, svc):
            base = await ingest(svc, "Leave is 25 days.", source="leave.md")
            section = await ingest(svc, "Leave is 20 days.", source="leave.md", section="onboarding")
            other = await ingest(svc, "Leave is 15 days.", source="leave.md", user="bob")
            assert len({base.id, section.id, other.id}) == 3
            assert (await svc.search(scope(), "25 days"))[0].document.id == base.id
            assert (await svc.search(scope(section="onboarding"), "20 days"))[0].document.id == section.id
            assert (await svc.search(scope("bob"), "15 days"))[0].document.id == other.id
    run(go())


def test_migration_is_idempotent(pg):
    dsn, schema = pg
    migrate(dsn, schema=schema)
    migrate(dsn, schema=schema)

    async def go():
        async with knowledge(dsn, schema) as (_, svc):
            assert await svc.list_documents(scope()) == []
    run(go())
