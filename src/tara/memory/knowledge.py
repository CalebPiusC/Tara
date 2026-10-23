"""Document RAG for approved company knowledge.

This module owns the policy and text handling: chunking, ranking, the service
timeouts/metrics, and the store contract. Storage implementations live in
`knowledge_store.py`. Authorization is enforced by the store *before* chunk text
is returned; the model can supply a query, never a tenant, user, section or ACL.

The supported formats are UTF-8 text and Markdown. There is no embedding model,
vector database, PDF parser or network crawler in this path. Retrieved text is
untrusted data, never instructions.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import re
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from ..core.contracts import Passage
from ..core.errors import PermissionDenied
from ..core.metrics import Metrics
from .models import MemoryScope, normalize_text, tokens, utcnow

DEFAULT_SEARCH_LIMIT = 4
DEFAULT_CONTEXT_CHARS = 2500
DEFAULT_CHUNK_CHARS = 1200
DEFAULT_OVERLAP_CHARS = 150
DEFAULT_MAX_DOCUMENT_CHARS = 2_000_000
DEFAULT_MAX_CHUNKS = 2000
MAX_TITLE_CHARS = 300
MAX_SOURCE_CHARS = 1024
MAX_QUERY_CHARS = 300
SUPPORTED_SUFFIXES = frozenset({".md", ".markdown", ".txt"})
ACL_PERMISSIONS = frozenset({"read"})

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass(frozen=True)
class KnowledgeDocument:
    """A document family. `scope.meeting_id` is its optional section, never a meeting run id."""

    id: str
    scope: MemoryScope
    title: str
    source: str
    approved: bool = True
    version: int = 1
    content_hash: str = ""
    effective_date: date | None = None
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    deleted_at: datetime | None = None

    @property
    def owner_user_id(self) -> str:
        return self.scope.user_id

    @property
    def section_id(self) -> str | None:
        return self.scope.meeting_id


@dataclass(frozen=True)
class ChunkDraft:
    heading: str
    text: str


@dataclass(frozen=True)
class DocumentDraft:
    title: str
    source: str
    content_hash: str
    approved: bool = True
    effective_date: date | None = None


@dataclass(frozen=True)
class KnowledgeChunk:
    id: str
    document_id: str
    version: int
    ordinal: int
    heading: str
    text: str

    def render(self) -> str:
        return f"{self.heading}\n{self.text}" if self.heading else self.text


@dataclass(frozen=True)
class KnowledgeHit:
    document: KnowledgeDocument
    chunk: KnowledgeChunk
    score: float = 0.0
    reason: str = ""

    @property
    def citation(self) -> str:
        parts = [self.document.title, f"v{self.document.version}"]
        if self.document.effective_date is not None:
            parts.append(self.document.effective_date.isoformat())
        if self.document.source:
            parts.append(self.document.source)
        return " — ".join(parts)

    def passage(self) -> Passage:
        return Passage(id=self.chunk.id, text=self.chunk.render(), source=self.citation)


class KnowledgeStore(Protocol):
    """Storage contract. A store must apply scope and ACL filters before returning chunks."""

    async def ingest(self, scope: MemoryScope, draft: DocumentDraft,
                     chunks: Sequence[ChunkDraft]) -> KnowledgeDocument: ...
    async def get(self, scope: MemoryScope, document_id: str, *,
                  approved_only: bool = True) -> KnowledgeDocument | None: ...
    async def search(self, scope: MemoryScope, query: str, limit: int = 40) -> list[KnowledgeHit]: ...
    async def list_documents(self, scope: MemoryScope, limit: int = 50, *,
                             include_unapproved: bool = False) -> list[KnowledgeDocument]: ...
    async def delete(self, scope: MemoryScope, document_id: str, *, hard: bool = False) -> bool: ...
    async def purge_deleted(self, scope: MemoryScope) -> int: ...
    async def set_approved(self, scope: MemoryScope, document_id: str, approved: bool) -> KnowledgeDocument: ...
    async def grant(self, scope: MemoryScope, document_id: str, subject_id: str,
                    permission: str = "read") -> None: ...
    async def revoke(self, scope: MemoryScope, document_id: str, subject_id: str,
                     permission: str = "read") -> None: ...
    async def close(self) -> None: ...


def normalize_document_text(text: str) -> str:
    """A deterministic, readable form. Control characters are removed, not interpreted."""
    clean = str(text).replace("\r\n", "\n").replace("\r", "\n")
    clean = _CONTROL.sub(" ", clean)
    lines = [line.rstrip() for line in clean.split("\n")]
    clean = "\n".join(lines)
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    return clean.strip()


def _tail(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return ""
    cut = text[-max_chars:]
    space = cut.find(" ")
    return cut[space + 1:].strip() if space >= 0 else cut.strip()


def _hard_split(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    """Split a sentence longer than one chunk at word boundaries, with a bounded overlap."""
    out: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + max_chars)
        if end < n:
            cut = text.rfind(" ", start, end)
            if cut > start + max_chars // 2:
                end = cut
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        if end >= n:
            break
        start = max(start + 1, end - max(0, overlap_chars))
        while start < n and start > 0 and text[start - 1] != " " and text[start] != " ":
            start += 1
    return out


def _split_paragraph(paragraph: str, max_chars: int, overlap_chars: int) -> list[str]:
    if len(paragraph) <= max_chars:
        return [paragraph]
    sentences = [s.strip() for s in _SENTENCE.split(paragraph) if s.strip()]
    out: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            if current:
                out.append(current)
                current = ""
            out.extend(_hard_split(sentence, max_chars, overlap_chars))
        elif not current:
            current = sentence
        elif len(current) + 1 + len(sentence) <= max_chars:
            current = f"{current} {sentence}"
        else:
            out.append(current)
            current = sentence
    if current:
        out.append(current)
    return out


def chunk_document(text: str, *, max_chars: int = DEFAULT_CHUNK_CHARS,
                   overlap_chars: int = DEFAULT_OVERLAP_CHARS,
                   max_chunks: int = DEFAULT_MAX_CHUNKS) -> tuple[ChunkDraft, ...]:
    """Split Markdown/text into heading-aware chunks of at most `max_chars`.

    Paragraph packing, sentence splitting and a bounded overlap keep a fact from
    being cut in half. The function is deterministic, so re-ingesting identical
    text produces the same chunk boundaries.
    """
    if max_chars < 200:
        raise ValueError("max_chars must be at least 200")
    if overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("overlap_chars must be between 0 and max_chars")
    clean = normalize_document_text(text)
    if not clean:
        raise ValueError("a document needs text")

    sections: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []
    heading = ""
    body: list[str] = []

    def flush() -> None:
        nonlocal body
        block = "\n".join(body).strip()
        if block:
            sections.append((heading, block))
        body = []

    for line in clean.split("\n"):
        match = _HEADING.match(line)
        if match is None:
            body.append(line)
            continue
        flush()
        level = len(match.group(1))
        title = match.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        heading = " > ".join(part for _, part in stack)

    flush()
    if not sections:
        sections.append(("", clean))

    chunks: list[ChunkDraft] = []
    for heading_path, block in sections:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", block) if p.strip()]
        pieces: list[str] = []
        current = ""
        for paragraph in paragraphs:
            for piece in _split_paragraph(paragraph, max_chars, overlap_chars):
                if not current:
                    current = piece
                elif len(current) + 2 + len(piece) <= max_chars:
                    current = f"{current}\n\n{piece}"
                else:
                    pieces.append(current)
                    overlap = _tail(current, overlap_chars)
                    candidate = f"{overlap}\n\n{piece}" if overlap else piece
                    current = candidate if len(candidate) <= max_chars else piece
        if current:
            pieces.append(current)
        for piece in pieces:
            piece = piece.strip()
            if piece:
                chunks.append(ChunkDraft(heading_path, piece))
                if len(chunks) > max_chunks:
                    raise ValueError(f"a document may produce at most {max_chunks} chunks")
    if not chunks:
        raise ValueError("a document needs text")
    return tuple(chunks)


def _haystack(hit: KnowledgeHit) -> str:
    return f"{hit.document.title} {hit.chunk.heading} {hit.chunk.text}"


def rank_hits(query: str, hits: Sequence[KnowledgeHit], *, scope: MemoryScope | None = None,
              limit: int = DEFAULT_SEARCH_LIMIT, max_chars: int = DEFAULT_CONTEXT_CHARS,
              max_per_document: int = 2) -> list[KnowledgeHit]:
    """Rarity-weighted keyword ranking with phrase, title and heading boosts.

    The store has already applied authorization. This function only chooses the
    best bounded evidence; it never widens a scope.
    """
    wanted = tokens(query)
    if not wanted or not hits or limit < 1:
        return []
    n = len(hits)
    document_frequency: Counter[str] = Counter()
    haystacks: dict[int, tuple[KnowledgeHit, str, set[str]]] = {}
    for hit in hits:
        body = _haystack(hit)
        body_tokens = tokens(body)
        haystacks[id(hit)] = (hit, body, body_tokens)
        for token in wanted:
            if token in body_tokens:
                document_frequency[token] += 1

    query_norm = normalize_text(query)
    scored: list[KnowledgeHit] = []
    for hit in hits:
        _, body, body_tokens = haystacks[id(hit)]
        matched = wanted & body_tokens
        if not matched:
            continue
        matched_idf = sum(1.0 + math.log((n + 1) / (document_frequency[token] + 1)) for token in matched)
        total_idf = sum(1.0 + math.log((n + 1) / (document_frequency[token] + 1)) for token in wanted)
        coverage = matched_idf / total_idf if total_idf else 0.0
        phrase = 0.18 if len(query_norm) >= 3 and query_norm in normalize_text(body) else 0.0
        title = 0.12 if wanted & tokens(hit.document.title) else 0.0
        heading = 0.08 if wanted & tokens(hit.chunk.heading) else 0.0
        section = (0.02 if scope is not None and hit.document.section_id is not None
                   and hit.document.section_id == scope.meeting_id else 0.0)
        scored.append(replace(hit, score=round(coverage + phrase + title + heading + section, 4),
                              reason="ranked"))

    scored.sort(key=lambda h: (-h.score, -h.document.updated_at.timestamp(), h.document.id, h.chunk.ordinal))
    out: list[KnowledgeHit] = []
    per_document: Counter[str] = Counter()
    chosen: set[str] = set()
    used_chars = 0

    def take(hit: KnowledgeHit) -> None:
        nonlocal used_chars
        if hit.chunk.id in chosen:
            return
        block_chars = len(hit.chunk.heading) + len(hit.chunk.text) + len(hit.document.title) + 64
        if out and used_chars + block_chars > max_chars:
            return
        if not out and block_chars > max_chars:
            room = max(1, max_chars - len(hit.chunk.heading) - len(hit.document.title) - 64)
            hit = replace(hit, chunk=replace(hit.chunk, text=hit.chunk.text[:room].rstrip() + " ..."))
            block_chars = len(hit.chunk.heading) + len(hit.chunk.text) + len(hit.document.title) + 64
        out.append(hit)
        chosen.add(hit.chunk.id)
        per_document[hit.document.id] += 1
        used_chars += block_chars

    # One passage per document first: a single long document must not crowd every other source out.
    for hit in scored:
        if per_document[hit.document.id] == 0:
            take(hit)
            if len(out) >= limit:
                break
    if len(out) < limit:
        for hit in scored:
            if per_document[hit.document.id] >= max_per_document:
                continue
            take(hit)
            if len(out) >= limit:
                break
    out.sort(key=lambda h: (-h.score, -h.document.updated_at.timestamp(), h.document.id, h.chunk.ordinal))
    return out


class KnowledgeService:
    """Validated ingestion and bounded retrieval over a `KnowledgeStore`."""

    def __init__(self, store: KnowledgeStore, *, metrics: Metrics | None = None,
                 search_timeout_s: float = 2.0, write_timeout_s: float = 5.0,
                 default_limit: int = DEFAULT_SEARCH_LIMIT, max_context_chars: int = DEFAULT_CONTEXT_CHARS,
                 chunk_chars: int = DEFAULT_CHUNK_CHARS, overlap_chars: int = DEFAULT_OVERLAP_CHARS,
                 max_document_chars: int = DEFAULT_MAX_DOCUMENT_CHARS,
                 max_chunks: int = DEFAULT_MAX_CHUNKS) -> None:
        self.store = store
        self.metrics = metrics or Metrics()
        self.search_timeout_s, self.write_timeout_s = search_timeout_s, write_timeout_s
        self.default_limit, self.max_context_chars = default_limit, max_context_chars
        self.chunk_chars, self.overlap_chars = chunk_chars, overlap_chars
        self.max_document_chars, self.max_chunks = max_document_chars, max_chunks

    def _document_scope(self, scope: MemoryScope, section_id: str | None) -> MemoryScope:
        return MemoryScope(scope.tenant_id, scope.user_id, meeting_id=section_id)

    async def ingest_text(self, scope: MemoryScope, title: str, text: str, *, source: str,
                          section_id: str | None = None, approved: bool = True,
                          effective_date: date | None = None) -> KnowledgeDocument:
        """Ingest one text/Markdown document. New content for the same source becomes a new version."""
        clean_title = (title or "").strip()[:MAX_TITLE_CHARS]
        clean_source = (source or "").strip()[:MAX_SOURCE_CHARS]
        if not clean_title:
            raise ValueError("a document needs a title")
        if not clean_source:
            raise ValueError("a document needs a source (path, URL or stable name)")
        if len(text) > self.max_document_chars:
            raise ValueError(f"a document may be at most {self.max_document_chars} characters")
        owner = self._document_scope(scope, section_id)
        chunks = chunk_document(text, max_chars=self.chunk_chars, overlap_chars=self.overlap_chars,
                                max_chunks=self.max_chunks)
        normalized = normalize_document_text(text)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        draft = DocumentDraft(clean_title, clean_source, digest, approved=approved, effective_date=effective_date)
        t0 = time.perf_counter()
        try:
            document = await asyncio.wait_for(self.store.ingest(owner, draft, chunks), self.write_timeout_s)
            self.metrics.inc("knowledge_documents_ingested")
            self.metrics.inc("knowledge_chunks_ingested", len(chunks))
            return document
        except asyncio.CancelledError:
            raise
        except Exception:
            self.metrics.inc("knowledge_ingest_failures")
            raise
        finally:
            self.metrics.observe("knowledge_ingest_ms", (time.perf_counter() - t0) * 1000)

    async def ingest_file(self, scope: MemoryScope, path: str | Path, *, title: str = "", source: str = "",
                          section_id: str | None = None, approved: bool = True,
                          effective_date: date | None = None) -> KnowledgeDocument:
        """Read one UTF-8 `.md`, `.markdown` or `.txt` file. Other formats are refused, not guessed at."""
        file_path = Path(path).expanduser()
        if not file_path.is_file():
            raise ValueError(f"not a file: {file_path}")
        if file_path.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise ValueError("document RAG supports UTF-8 .txt, .md and .markdown files only")
        data = file_path.read_bytes()
        if len(data) > self.max_document_chars * 4:
            raise ValueError(f"file is too large (limit {self.max_document_chars * 4} bytes)")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("document is not UTF-8 text") from exc
        resolved = file_path.resolve()
        return await self.ingest_text(
            scope, title.strip() or file_path.stem, text,
            source=source.strip() or str(resolved), section_id=section_id, approved=approved,
            effective_date=effective_date)

    async def search(self, scope: MemoryScope, query: str, *, limit: int | None = None,
                     max_chars: int | None = None) -> list[KnowledgeHit]:
        """Return only approved, authorized chunks. Failure is reported, never disguised as "no documents"."""
        clean_query = query.strip()[:MAX_QUERY_CHARS]
        if not clean_query:
            return []
        use_limit = max(1, min(limit or self.default_limit, 10))
        candidates = max(use_limit * 8, 30)
        t0 = time.perf_counter()
        try:
            hits = await asyncio.wait_for(self.store.search(scope, clean_query, candidates), self.search_timeout_s)
            ranked = rank_hits(clean_query, hits, scope=scope, limit=use_limit,
                               max_chars=max_chars or self.max_context_chars)
            self.metrics.inc("knowledge_searches")
            return ranked
        except asyncio.CancelledError:
            raise
        except Exception:
            self.metrics.inc("knowledge_search_failures")
            raise
        finally:
            self.metrics.observe("knowledge_search_ms", (time.perf_counter() - t0) * 1000)

    async def get(self, scope: MemoryScope, document_id: str, *,
                  approved_only: bool = True) -> KnowledgeDocument | None:
        return await asyncio.wait_for(self.store.get(scope, document_id, approved_only=approved_only),
                                      self.search_timeout_s)

    async def list_documents(self, scope: MemoryScope, *, limit: int = 50,
                             include_unapproved: bool = False) -> list[KnowledgeDocument]:
        return await asyncio.wait_for(
            self.store.list_documents(scope, max(1, min(limit, 200)), include_unapproved=include_unapproved),
            self.search_timeout_s)

    async def _owned(self, scope: MemoryScope, document_id: str) -> KnowledgeDocument:
        document = await self.get(scope, document_id, approved_only=False)
        if document is None:
            raise KeyError("document not found")
        if document.owner_user_id != scope.user_id:
            raise PermissionDenied("only the document owner can manage or share it")
        return document

    async def delete(self, scope: MemoryScope, document_id: str, *, hard: bool = False) -> bool:
        document = await self.get(scope, document_id, approved_only=False)
        if document is None:
            return False
        if document.owner_user_id != scope.user_id:
            raise PermissionDenied("only the document owner can delete it")
        deleted = await asyncio.wait_for(self.store.delete(scope, document_id, hard=hard), self.write_timeout_s)
        if deleted:
            self.metrics.inc("knowledge_documents_deleted")
        return deleted

    async def set_approved(self, scope: MemoryScope, document_id: str, approved: bool) -> KnowledgeDocument:
        await self._owned(scope, document_id)
        document = await asyncio.wait_for(self.store.set_approved(scope, document_id, approved),
                                          self.write_timeout_s)
        self.metrics.inc("knowledge_documents_approved" if approved else "knowledge_documents_unapproved")
        return document

    async def grant(self, scope: MemoryScope, document_id: str, subject_id: str,
                    permission: str = "read") -> None:
        if permission not in ACL_PERMISSIONS:
            raise ValueError(f"unknown document permission {permission!r}")
        subject = subject_id.strip()
        if not subject:
            raise ValueError("a share needs a subject user id")
        await self._owned(scope, document_id)
        await asyncio.wait_for(self.store.grant(scope, document_id, subject, permission), self.write_timeout_s)
        self.metrics.inc("knowledge_documents_shared")

    async def revoke(self, scope: MemoryScope, document_id: str, subject_id: str,
                     permission: str = "read") -> None:
        if permission not in ACL_PERMISSIONS:
            raise ValueError(f"unknown document permission {permission!r}")
        subject = subject_id.strip()
        if not subject:
            raise ValueError("a revocation needs a subject user id")
        await self._owned(scope, document_id)
        await asyncio.wait_for(self.store.revoke(scope, document_id, subject, permission), self.write_timeout_s)
        self.metrics.inc("knowledge_documents_unshared")

    async def purge_deleted(self, scope: MemoryScope) -> int:
        purged = await asyncio.wait_for(self.store.purge_deleted(scope), self.write_timeout_s)
        if purged:
            self.metrics.inc("knowledge_documents_purged", purged)
        return purged

    async def close(self) -> None:
        await self.store.close()
