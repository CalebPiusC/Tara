"""Offline document RAG: chunking, approval, ACLs, versioning, deletion and the live tool."""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from tara.core.errors import PermissionDenied
from tara.core.metrics import Metrics
from tara.memory import InMemoryKnowledgeStore, KnowledgeService, MemoryScope
from tara.memory.knowledge import DEFAULT_CONTEXT_CHARS, chunk_document, rank_hits
from tara.memory.knowledge_tools import (
    EMPTY_RESULT_NOTE,
    SEARCH_KNOWLEDGE_TOOL,
    knowledge_tool_grants,
    search_knowledge_tool,
)

run = asyncio.run


def service(**kw) -> KnowledgeService:
    return KnowledgeService(InMemoryKnowledgeStore(), **kw)


def scope(user: str = "u1", tenant: str = "t1", section: str | None = None) -> MemoryScope:
    return MemoryScope(tenant, user, meeting_id=section)


async def ingest(svc: KnowledgeService, text: str, *, source: str = "handbook.md", title: str = "Handbook",
                 user: str = "u1", tenant: str = "t1", section: str | None = None,
                 approved: bool = True):
    return await svc.ingest_text(scope(user, tenant, section), title, text, source=source,
                                 section_id=section, approved=approved)


def test_chunking_is_heading_aware_and_bounded():
    text = "# Refunds\n\n" + ("Refund policy sentence with useful detail. " * 90) + "\n\n## Gift cards\n\nGift cards are final."
    chunks = chunk_document(text, max_chars=300, overlap_chars=40)
    assert chunks
    assert all(len(c.text) <= 300 for c in chunks)
    assert chunks[0].heading == "Refunds"
    assert chunks[-1].heading == "Refunds > Gift cards"
    assert "Gift cards are final." in chunks[-1].text
    assert "Refund policy" in " ".join(c.text for c in chunks)


def test_ingest_search_citation_and_provenance():
    async def go():
        svc = service()
        document = await ingest(svc, "Refunds are processed within 30 days.", title="Refund Policy",
                                source="policies/refunds.md", section=None)
        hits = await svc.search(scope(), "How long is the refund?")
        assert len(hits) == 1
        hit = hits[0]
        assert hit.document.id == document.id and hit.chunk.document_id == document.id
        assert hit.document.title == "Refund Policy"
        assert "v1" in hit.citation and "policies/refunds.md" in hit.citation
        assert "30 days" in hit.chunk.text
        assert hit.chunk.id in hit.passage().id
        assert hit.passage().source == hit.citation
    run(go())


def test_effective_date_is_shown_and_searchable():
    async def go():
        svc = service()
        await svc.ingest_text(scope(), "Refund Policy", "The refund window is 30 days.", source="refund.md",
                              effective_date=date(2026, 1, 5))
        hit = (await svc.search(scope(), "refund window"))[0]
        assert "2026-01-05" in hit.citation
    run(go())


def test_unapproved_draft_is_invisible_until_approved():
    async def go():
        svc = service()
        document = await ingest(svc, "Secret pricing is 42 units.", source="pricing.md", approved=False)
        assert await svc.search(scope(), "secret pricing") == []
        assert await svc.list_documents(scope()) == []
        assert [d.id for d in await svc.list_documents(scope(), include_unapproved=True)] == [document.id]
        approved = await svc.set_approved(scope(), document.id, True)
        assert approved.approved is True
        assert (await svc.search(scope(), "secret pricing"))[0].document.id == document.id
        await svc.set_approved(scope(), document.id, False)
        assert await svc.search(scope(), "secret pricing") == []
    run(go())


def test_tenant_and_user_isolation_with_canaries():
    async def go():
        svc = service()
        document = await ingest(svc, "CANARY-ALICE refund window is 30 days.", source="alice.md", user="alice")
        assert await svc.search(scope("bob"), "CANARY-ALICE") == []
        assert await svc.search(scope("alice", "t2"), "CANARY-ALICE") == []
        assert await svc.get(scope("bob"), document.id) is None
        assert [h.document.id for h in await svc.search(scope("alice"), "CANARY-ALICE")] == [document.id]
    run(go())


def test_section_scoped_documents_are_only_visible_in_that_section():
    async def go():
        svc = service()
        global_doc = await ingest(svc, "All staff receive 25 days of leave.", source="global.md")
        section_doc = await ingest(svc, "Onboarding leave is 20 days.", source="onboarding.md", section="onboarding")
        in_onboarding = await svc.search(scope(section="onboarding"), "leave")
        assert {h.document.id for h in in_onboarding} == {global_doc.id, section_doc.id}
        in_other = await svc.search(scope(section="sales"), "leave")
        assert {h.document.id for h in in_other} == {global_doc.id}
        everywhere = await svc.search(scope(), "leave")
        assert {h.document.id for h in everywhere} == {global_doc.id, section_doc.id}
    run(go())


def test_acl_share_and_revoke_are_owner_controlled_and_tenant_bound():
    async def go():
        svc = service()
        document = await ingest(svc, "CANARY-SHARED refund window is 30 days.", source="shared.md", user="alice")
        alice, bob = scope("alice"), scope("bob")
        assert await svc.search(bob, "CANARY-SHARED") == []
        await svc.grant(alice, document.id, "bob")
        assert (await svc.search(bob, "CANARY-SHARED"))[0].document.id == document.id
        with pytest.raises(PermissionDenied):
            await svc.delete(bob, document.id)
        with pytest.raises(PermissionDenied):
            await svc.grant(bob, document.id, "carol")
        await svc.revoke(alice, document.id, "bob")
        assert await svc.search(bob, "CANARY-SHARED") == []

        # A grant carries the document's tenant; it cannot be used from another tenant.
        other_tenant = scope("bob", "t2")
        await svc.grant(alice, document.id, "bob")
        assert await svc.search(other_tenant, "CANARY-SHARED") == []
    run(go())


def test_a_shared_draft_stays_invisible_until_approved():
    async def go():
        svc = service()
        document = await ingest(svc, "DRAFT-CANARY refund window is 12 days.", source="draft.md",
                                approved=False, user="alice")
        await svc.grant(scope("alice"), document.id, "bob")
        assert await svc.search(scope("bob"), "DRAFT-CANARY") == []
        await svc.set_approved(scope("alice"), document.id, True)
        assert (await svc.search(scope("bob"), "DRAFT-CANARY"))[0].document.id == document.id
    run(go())


def test_new_content_versions_and_old_chunks_are_not_current():
    async def go():
        svc = service()
        first = await ingest(svc, "The refund window is 30 days.", source="refund.md")
        second = await ingest(svc, "The refund window is 14 days.", source="refund.md")
        assert second.id == first.id and second.version == 2
        assert "30 days" not in " ".join(h.chunk.text for h in await svc.search(scope(), "refund"))
        hits = await svc.search(scope(), "14 days")
        assert hits and hits[0].document.version == 2 and "14 days" in hits[0].chunk.text
        current = await svc.get(scope(), first.id)
        assert current is not None and current.version == 2

        # Re-ingesting identical content is a no-op, not a new version.
        third = await ingest(svc, "The refund window is 14 days.", source="refund.md")
        assert third.version == 2

        # Reverting to the old content reuses its immutable version.
        reverted = await ingest(svc, "The refund window is 30 days.", source="refund.md")
        assert reverted.version == 1
        assert "30 days" in (await svc.search(scope(), "30 days"))[0].chunk.text
    run(go())


def test_same_source_in_another_section_or_user_is_a_separate_document():
    async def go():
        svc = service()
        base = await ingest(svc, "Leave is 25 days.", source="leave.md")
        section = await ingest(svc, "Leave is 20 days.", source="leave.md", section="onboarding")
        other = await ingest(svc, "Leave is 15 days.", source="leave.md", user="bob")
        assert len({base.id, section.id, other.id}) == 3
        assert (await svc.search(scope(), "25 days"))[0].document.id == base.id
        assert (await svc.search(scope(section="onboarding"), "20 days"))[0].document.id == section.id
        assert (await svc.search(scope("bob"), "15 days"))[0].document.id == other.id
    run(go())


def test_soft_delete_purge_and_reingest():
    async def go():
        svc = service()
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
        assert await svc.delete(scope(), replacement.id) is False
    run(go())


def test_ingest_file_reads_utf8_markdown_and_refuses_other_formats(tmp_path):
    async def go():
        path = tmp_path / "handbook.md"
        path.write_text("# Travel\n\nBook flights in advance.", encoding="utf-8")
        svc = service()
        document = await svc.ingest_file(scope(), path)
        assert document.title == "handbook"
        assert str(path.resolve()) in document.source
        assert (await svc.search(scope(), "book flights"))[0].document.id == document.id

        pdf = tmp_path / "cv.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        with pytest.raises(ValueError, match="supports"):
            await svc.ingest_file(scope(), pdf)

        broken = tmp_path / "broken.txt"
        broken.write_bytes(b"\xff\xfe\x00")
        with pytest.raises(ValueError, match="UTF-8"):
            await svc.ingest_file(scope(), broken)
    run(go())


def test_oversized_documents_are_rejected():
    async def go():
        svc = service(max_document_chars=100)
        with pytest.raises(ValueError, match="at most"):
            await svc.ingest_text(scope(), "Too big", "x" * 101, source="big.md")
    run(go())


def test_empty_query_and_no_match_return_empty():
    async def go():
        svc = service()
        await ingest(svc, "Refunds take 30 days.", source="refund.md")
        assert await svc.search(scope(), "") == []
        assert await svc.search(scope(), "quantum flux") == []
    run(go())


def test_ranking_keeps_multiple_documents_and_respects_the_context_budget():
    async def go():
        svc = service(chunk_chars=200, overlap_chars=40, max_context_chars=600)
        for i in range(4):
            await ingest(svc, f"Policy section {i}: " + ("leave policy detail. " * 40), source=f"p{i}.md",
                         title=f"Policy {i}")
        hits = await svc.search(scope(), "leave policy", limit=10, max_chars=600)
        assert hits
        assert len({h.document.id for h in hits}) >= 2
        assert sum(len(h.chunk.heading) + len(h.chunk.text) for h in hits) <= 600
    run(go())


def test_rank_hits_boosts_exact_phrase_and_title():
    async def go():
        svc = service()
        await ingest(svc, "The cancellation window is fourteen days.", source="cancel.md", title="Cancellation Policy")
        await ingest(svc, "Cancellation is discussed in the general meeting notes.", source="notes.md",
                     title="Meeting notes")
        hits = await svc.search(scope(), "cancellation window")
        assert hits[0].document.title == "Cancellation Policy"
        assert hits[0].score > 0
    run(go())


def test_live_tool_is_scope_bound_and_distinguishes_empty_from_failure():
    async def go():
        svc = service()
        document = await ingest(svc, "CANARY-TOOL refund window is 30 days.", source="tool.md", user="alice")
        definition = search_knowledge_tool(svc, scope("alice"))
        assert definition.name == SEARCH_KNOWLEDGE_TOOL
        result = await definition.handler("CANARY-TOOL")
        assert isinstance(result, dict)
        assert result["count"] == 1
        passage = result["passages"][0]
        assert passage["document_id"] == document.id
        assert passage["citation"].startswith("Handbook")

        # The scope is bound in the closure: another user cannot ask for Alice's document.
        other = search_knowledge_tool(svc, scope("bob"))
        assert (await other.handler("CANARY-TOOL")) == {"count": 0, "passages": [], "note": EMPTY_RESULT_NOTE}

        grants = knowledge_tool_grants(svc, scope("alice"))
        assert grants[0][0].name == SEARCH_KNOWLEDGE_TOOL and grants[0][1].scope == "knowledge"
        assert grants[0][0].side_effect is False
    run(go())


def test_search_failure_is_reported_and_counted():
    class Broken:
        async def search(self, scope, query, limit=40):
            raise RuntimeError("database unavailable")

    async def go():
        metrics = Metrics()
        svc = KnowledgeService(Broken(), metrics=metrics)
        with pytest.raises(RuntimeError):
            await svc.search(scope(), "refund")
        assert metrics.counters["knowledge_search_failures"] == 1
    run(go())


def test_scope_and_owner_validation():
    with pytest.raises(ValueError):
        MemoryScope("t", "u", meeting_id="Not A Slug")
    async def go():
        svc = service()
        document = await ingest(svc, "Only the owner can approve.", source="owner.md", user="alice")
        await svc.grant(scope("alice"), document.id, "bob")
        with pytest.raises(PermissionDenied):
            await svc.set_approved(scope("bob"), document.id, True)
        assert await svc.delete(scope("alice"), "00000000-0000-0000-0000-000000000000") is False
    run(go())


def test_rank_hits_is_bounded_without_a_store():
    assert rank_hits("refund", [], scope=scope()) == []
    assert DEFAULT_CONTEXT_CHARS >= 200
