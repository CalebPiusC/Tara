"""Storage for document RAG.

`InMemoryKnowledgeStore` is for offline tests and single-process use.
`PostgresKnowledgeStore` is the durable implementation. Both apply the tenant,
user, section and per-document ACL filters *before* returning chunk text.

There is deliberately no embedding column, vector index or network crawler: the
search leg is PostgreSQL full-text search and a deterministic Python re-ranker.
"""
from __future__ import annotations

import asyncio
import importlib.util
import re
from collections.abc import Sequence
from typing import Any

from ..core.errors import ConfigError
from .knowledge import ChunkDraft, DocumentDraft, KnowledgeChunk, KnowledgeDocument, KnowledgeHit
from .models import MemoryScope, new_id, tokens, utcnow

_LIKE_SPECIAL = re.compile(r"([%_\\])")


def _escape_like(value: str) -> str:
    return _LIKE_SPECIAL.sub(r"\\\1", value)


def _scope(row: dict[str, Any]) -> MemoryScope:
    return MemoryScope(str(row["tenant_id"]), str(row["owner_user_id"]), meeting_id=row["section_id"] or None)


def _document(row: dict[str, Any]) -> KnowledgeDocument:
    return KnowledgeDocument(
        id=str(row["id"]), scope=_scope(row), title=str(row["title"]), source=str(row["source"]),
        approved=bool(row["approved"]), version=int(row.get("version") or 0),
        content_hash=str(row.get("content_hash") or ""), effective_date=row.get("effective_date"),
        created_at=row["created_at"], updated_at=row["updated_at"], deleted_at=row.get("deleted_at"))


def _row_to_hit(row: dict[str, Any]) -> KnowledgeHit:
    document = _document(row)
    chunk = KnowledgeChunk(
        id=str(row["chunk_id"]), document_id=document.id, version=int(row.get("version") or 0),
        ordinal=int(row["ordinal"]), heading=str(row.get("heading_path") or ""), text=str(row["text"]))
    return KnowledgeHit(document, chunk, float(row.get("rank") or 0.0), "fts")


class InMemoryKnowledgeStore:
    """Deterministic in-process store; authorization and section rules mirror the SQL store."""

    def __init__(self) -> None:
        self._documents: dict[str, KnowledgeDocument] = {}
        self._versions: dict[str, dict[int, list[KnowledgeChunk]]] = {}
        self._hashes: dict[str, dict[str, int]] = {}
        self._grants: set[tuple[str, str, str, str]] = set()
        self._lock = asyncio.Lock()

    def _find_identity(self, scope: MemoryScope, source: str) -> KnowledgeDocument | None:
        return next((d for d in self._documents.values()
                     if d.deleted_at is None and d.scope.tenant_id == scope.tenant_id
                     and d.owner_user_id == scope.user_id and d.section_id == scope.meeting_id
                     and d.source == source), None)

    def _readable(self, scope: MemoryScope, document: KnowledgeDocument, *, approved_only: bool) -> bool:
        if document.deleted_at is not None or document.scope.tenant_id != scope.tenant_id:
            return False
        if approved_only and not document.approved:
            return False
        if not approved_only and not document.approved and document.owner_user_id != scope.user_id:
            return False
        if scope.meeting_id is not None and document.section_id is not None \
                and document.section_id != scope.meeting_id:
            return False
        if document.owner_user_id == scope.user_id:
            return True
        return (document.scope.tenant_id, document.id, scope.user_id, "read") in self._grants

    def _chunks(self, document: KnowledgeDocument) -> list[KnowledgeChunk]:
        return self._versions.get(document.id, {}).get(document.version, [])

    async def ingest(self, scope: MemoryScope, draft: DocumentDraft,
                     chunks: Sequence[ChunkDraft]) -> KnowledgeDocument:
        if not chunks:
            raise ValueError("a document needs at least one chunk")
        async with self._lock:
            now = utcnow()
            existing = self._find_identity(scope, draft.source)
            if existing is not None:
                if existing.content_hash == draft.content_hash:
                    updated = KnowledgeDocument(
                        **{**existing.__dict__, "title": draft.title, "approved": draft.approved,
                           "effective_date": draft.effective_date, "updated_at": now})
                    self._documents[existing.id] = updated
                    return updated
                known = self._hashes.get(existing.id, {}).get(draft.content_hash)
                if known is not None:
                    updated = KnowledgeDocument(
                        **{**existing.__dict__, "title": draft.title, "approved": draft.approved,
                           "effective_date": draft.effective_date, "version": known, "updated_at": now})
                    self._documents[existing.id] = updated
                    return updated
                version = max(self._versions.get(existing.id, {})) + 1
                document = KnowledgeDocument(
                    **{**existing.__dict__, "title": draft.title, "approved": draft.approved,
                       "effective_date": draft.effective_date, "version": version,
                       "content_hash": draft.content_hash, "updated_at": now})
            else:
                version = 1
                document = KnowledgeDocument(
                    new_id(), scope, draft.title, draft.source, approved=draft.approved, version=version,
                    content_hash=draft.content_hash, effective_date=draft.effective_date,
                    created_at=now, updated_at=now)
            stored = [KnowledgeChunk(new_id(), document.id, version, i, c.heading, c.text)
                      for i, c in enumerate(chunks)]
            self._documents[document.id] = document
            self._versions.setdefault(document.id, {})[version] = stored
            self._hashes.setdefault(document.id, {})[draft.content_hash] = version
            return document

    async def get(self, scope: MemoryScope, document_id: str, *,
                  approved_only: bool = True) -> KnowledgeDocument | None:
        document = self._documents.get(document_id)
        return document if document is not None and self._readable(scope, document, approved_only=approved_only) else None

    async def search(self, scope: MemoryScope, query: str, limit: int = 40) -> list[KnowledgeHit]:
        wanted = tokens(query)
        if not wanted:
            return []
        out: list[KnowledgeHit] = []
        for document in self._documents.values():
            if not self._readable(scope, document, approved_only=True):
                continue
            for chunk in self._chunks(document):
                body = f"{document.title} {chunk.heading} {chunk.text}"
                matched = wanted & tokens(body)
                if matched:
                    out.append(KnowledgeHit(document, chunk, float(len(matched)), "keyword"))
                    if len(out) >= max(1, limit):
                        return out
        return out

    async def list_documents(self, scope: MemoryScope, limit: int = 50, *,
                             include_unapproved: bool = False) -> list[KnowledgeDocument]:
        docs = [d for d in self._documents.values()
                if self._readable(scope, d, approved_only=not include_unapproved)]
        docs.sort(key=lambda d: (-d.updated_at.timestamp(), d.id))
        return docs[:max(1, limit)]

    async def delete(self, scope: MemoryScope, document_id: str, *, hard: bool = False) -> bool:
        async with self._lock:
            document = self._documents.get(document_id)
            if document is None or document.scope.tenant_id != scope.tenant_id \
                    or document.owner_user_id != scope.user_id:
                return False
            if hard:
                self._documents.pop(document_id, None)
                self._versions.pop(document_id, None)
                self._hashes.pop(document_id, None)
                self._grants = {g for g in self._grants if g[1] != document_id}
            else:
                self._documents[document_id] = KnowledgeDocument(
                    **{**document.__dict__, "deleted_at": utcnow(), "updated_at": utcnow()})
            return True

    async def purge_deleted(self, scope: MemoryScope) -> int:
        async with self._lock:
            doomed = [d.id for d in self._documents.values()
                      if d.deleted_at is not None and d.scope.tenant_id == scope.tenant_id
                      and d.owner_user_id == scope.user_id]
            for document_id in doomed:
                self._documents.pop(document_id, None)
                self._versions.pop(document_id, None)
                self._hashes.pop(document_id, None)
                self._grants = {g for g in self._grants if g[1] != document_id}
            return len(doomed)

    async def set_approved(self, scope: MemoryScope, document_id: str, approved: bool) -> KnowledgeDocument:
        async with self._lock:
            document = self._documents.get(document_id)
            if document is None or document.scope.tenant_id != scope.tenant_id \
                    or document.owner_user_id != scope.user_id:
                raise KeyError("document not found")
            updated = KnowledgeDocument(**{**document.__dict__, "approved": approved, "updated_at": utcnow()})
            self._documents[document_id] = updated
            return updated

    async def grant(self, scope: MemoryScope, document_id: str, subject_id: str,
                    permission: str = "read") -> None:
        async with self._lock:
            document = self._documents.get(document_id)
            if document is None or document.scope.tenant_id != scope.tenant_id \
                    or document.owner_user_id != scope.user_id:
                raise KeyError("document not found")
            if permission != "read":
                raise ValueError("only read grants are supported")
            if subject_id.strip():
                self._grants.add((scope.tenant_id, document_id, subject_id.strip(), permission))

    async def revoke(self, scope: MemoryScope, document_id: str, subject_id: str,
                     permission: str = "read") -> None:
        async with self._lock:
            document = self._documents.get(document_id)
            if document is None or document.scope.tenant_id != scope.tenant_id \
                    or document.owner_user_id != scope.user_id:
                raise KeyError("document not found")
            self._grants.discard((scope.tenant_id, document_id, subject_id.strip(), permission))

    async def close(self) -> None:
        return None


class PostgresKnowledgeStore:
    """Durable document store. Every query filters tenant and owner/ACL before content leaves the database."""

    def __init__(self, dsn: str, *, schema: str | None = None, min_size: int = 1, max_size: int = 4,
                 timeout_s: float = 5.0) -> None:
        if not dsn.startswith(("postgresql://", "postgres://")):
            raise ConfigError("knowledge database URL must be a postgresql:// or postgres:// URL")
        if importlib.util.find_spec("psycopg") is None or importlib.util.find_spec("psycopg_pool") is None:
            raise ConfigError("PostgreSQL knowledge needs the optional 'memory' extra: pip install -e '.[memory]'")
        self._dsn, self._schema = dsn, schema
        self._min_size, self._max_size, self._timeout_s = min_size, max_size, timeout_s
        self._pool: Any = None
        self._lock = asyncio.Lock()

    async def _ensure(self) -> Any:
        if self._pool is not None:
            return self._pool
        async with self._lock:
            if self._pool is not None:
                return self._pool
            try:
                from psycopg.rows import dict_row
                from psycopg_pool import AsyncConnectionPool
            except ImportError as exc:  # pragma: no cover - only without the extra
                raise ConfigError("PostgreSQL knowledge needs the optional 'memory' extra: pip install -e '.[memory]'") from exc
            kwargs: dict[str, Any] = {"row_factory": dict_row}
            if self._schema:
                kwargs["options"] = f"-csearch_path={self._schema}"
            pool = AsyncConnectionPool(conninfo=self._dsn, min_size=self._min_size, max_size=self._max_size,
                                       open=False, kwargs=kwargs)
            await pool.open(wait=True, timeout=self._timeout_s)
            self._pool = pool
            return pool

    @staticmethod
    def _read_predicate(approved_only: bool) -> str:
        approval = "s.approved" if approved_only else "(s.approved OR s.owner_user_id = %(user_id)s)"
        return f"""
            s.deleted_at IS NULL
            AND {approval}
            AND s.tenant_id = %(tenant_id)s
            AND (%(section_id)s::text IS NULL OR s.section_id IS NULL OR s.section_id = %(section_id)s)
            AND (
                s.owner_user_id = %(user_id)s
                OR EXISTS (
                    SELECT 1 FROM acl_grants a
                    WHERE a.resource_id = s.id AND a.resource_type = 'document'
                      AND a.tenant_id = s.tenant_id AND a.subject_type = 'user'
                      AND a.subject_id = %(user_id)s AND a.permission IN ('read')
                )
            )
        """

    async def _fetch_document(self, conn: Any, scope: MemoryScope, document_id: str, *,
                              approved_only: bool = False) -> KnowledgeDocument | None:
        cur = await conn.execute(
            f"""SELECT s.*, coalesce(v.version, 0) AS version
                FROM knowledge_sources s
                LEFT JOIN document_versions v
                  ON v.source_id = s.id AND v.version = s.current_version AND v.status = 'indexed'
                WHERE s.id = %(id)s AND {self._read_predicate(approved_only)}""",
            {"id": document_id, "tenant_id": scope.tenant_id, "user_id": scope.user_id,
             "section_id": scope.meeting_id})
        row = await cur.fetchone()
        return _document(row) if row else None

    async def ingest(self, scope: MemoryScope, draft: DocumentDraft,
                     chunks: Sequence[ChunkDraft]) -> KnowledgeDocument:
        if not chunks:
            raise ValueError("a document needs at least one chunk")
        pool = await self._ensure()
        async with pool.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    """SELECT id, current_version, content_hash, created_at
                       FROM knowledge_sources
                       WHERE tenant_id = %(tenant_id)s AND owner_user_id = %(user_id)s
                         AND coalesce(section_id, '') = coalesce(%(section_id)s, '') AND source = %(source)s
                         AND deleted_at IS NULL
                       FOR UPDATE""",
                    {"tenant_id": scope.tenant_id, "user_id": scope.user_id,
                     "section_id": scope.meeting_id, "source": draft.source})
                row = await cur.fetchone()
                now = utcnow()
                if row is None:
                    document_id = new_id()
                    await conn.execute(
                        """INSERT INTO knowledge_sources
                               (id, tenant_id, owner_user_id, section_id, title, source, approved,
                                current_version, content_hash, effective_date, created_at, updated_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, 0, '', %s, %s, %s)""",
                        (document_id, scope.tenant_id, scope.user_id, scope.meeting_id, draft.title,
                         draft.source, draft.approved, draft.effective_date, now, now))
                    version = 1
                else:
                    document_id = str(row["id"])
                    if str(row["content_hash"]) == draft.content_hash:
                        await conn.execute(
                            """UPDATE knowledge_sources
                               SET title=%s, approved=%s, effective_date=%s, updated_at=%s
                               WHERE id=%s AND tenant_id=%s AND owner_user_id=%s""",
                            (draft.title, draft.approved, draft.effective_date, now, document_id,
                             scope.tenant_id, scope.user_id))
                        found = await self._fetch_document(conn, scope, document_id)
                        if found is None:  # pragma: no cover - the row was just updated under a lock
                            raise RuntimeError("ingested document disappeared")
                        return found
                    cur = await conn.execute(
                        "SELECT version FROM document_versions WHERE source_id=%s AND content_hash=%s",
                        (document_id, draft.content_hash))
                    known = await cur.fetchone()
                    if known is not None:
                        version = int(known["version"])
                        await conn.execute(
                            """UPDATE document_versions SET status='superseded'
                               WHERE source_id=%s AND version<>%s""", (document_id, version))
                        await conn.execute(
                            "UPDATE document_versions SET status='indexed' WHERE source_id=%s AND version=%s",
                            (document_id, version))
                        await conn.execute(
                            """UPDATE knowledge_sources
                               SET title=%s, approved=%s, effective_date=%s, current_version=%s,
                                   content_hash=%s, updated_at=%s
                               WHERE id=%s""",
                            (draft.title, draft.approved, draft.effective_date, version, draft.content_hash,
                             now, document_id))
                        found = await self._fetch_document(conn, scope, document_id)
                        if found is None:  # pragma: no cover
                            raise RuntimeError("ingested document disappeared")
                        return found
                    version = int(row["current_version"]) + 1

                version_id = new_id()
                await conn.execute(
                    """INSERT INTO document_versions
                           (id, tenant_id, source_id, version, content_hash, status, effective_date, created_at)
                       VALUES (%s, %s, %s, %s, %s, 'indexed', %s, %s)""",
                    (version_id, scope.tenant_id, document_id, version, draft.content_hash,
                     draft.effective_date, now))
                async with conn.cursor() as cur:
                    await cur.executemany(
                        """INSERT INTO document_chunks
                               (id, tenant_id, source_id, version_id, ordinal, heading_path, text)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        [(new_id(), scope.tenant_id, document_id, version_id, i, c.heading, c.text)
                         for i, c in enumerate(chunks)])
                await conn.execute(
                    """UPDATE document_versions SET status='superseded'
                       WHERE source_id=%s AND version<>%s""", (document_id, version))
                await conn.execute(
                    """UPDATE knowledge_sources
                       SET title=%s, approved=%s, effective_date=%s, current_version=%s,
                           content_hash=%s, updated_at=%s
                       WHERE id=%s AND tenant_id=%s AND owner_user_id=%s""",
                    (draft.title, draft.approved, draft.effective_date, version, draft.content_hash,
                     now, document_id, scope.tenant_id, scope.user_id))
                found = await self._fetch_document(conn, scope, document_id)
                if found is None:  # pragma: no cover
                    raise RuntimeError("ingested document disappeared")
                return found

    async def get(self, scope: MemoryScope, document_id: str, *,
                  approved_only: bool = True) -> KnowledgeDocument | None:
        pool = await self._ensure()
        async with pool.connection() as conn:
            return await self._fetch_document(conn, scope, document_id, approved_only=approved_only)

    async def search(self, scope: MemoryScope, query: str, limit: int = 40) -> list[KnowledgeHit]:
        pool = await self._ensure()
        params = {"query": query, "like": f"%{_escape_like(query)}%", "tenant_id": scope.tenant_id,
                  "user_id": scope.user_id, "section_id": scope.meeting_id, "limit": max(1, min(limit, 100))}
        base = f"""
            SELECT c.id AS chunk_id, c.ordinal, c.heading_path, c.text,
                   v.version, s.*, ts_rank_cd(c.search, q) AS rank
            FROM document_chunks c
            JOIN document_versions v ON v.id = c.version_id
            JOIN knowledge_sources s ON s.id = c.source_id
            CROSS JOIN {{query_fn}}('english', {{query_param}}) q
            WHERE {self._read_predicate(True)}
              AND v.status = 'indexed' AND v.version = s.current_version
              AND (c.search @@ q OR to_tsvector('english', s.title) @@ q
                   OR c.text ILIKE %(like)s OR s.title ILIKE %(like)s)
            ORDER BY rank DESC, s.updated_at DESC, c.ordinal
            LIMIT %(limit)s
        """
        sql = base.format(query_fn="websearch_to_tsquery", query_param="%(query)s")
        async with pool.connection() as conn:
            cur = await conn.execute(sql, params)
            rows = await cur.fetchall()
            words = sorted(tokens(query))
            if not rows and len(words) > 1:
                params["query"] = " | ".join(words[:8])
                sql = base.format(query_fn="to_tsquery", query_param="%(query)s")
                cur = await conn.execute(sql, params)
                rows = await cur.fetchall()
        return [_row_to_hit(row) for row in rows]

    async def list_documents(self, scope: MemoryScope, limit: int = 50, *,
                             include_unapproved: bool = False) -> list[KnowledgeDocument]:
        pool = await self._ensure()
        async with pool.connection() as conn:
            cur = await conn.execute(
                f"""SELECT s.*, coalesce(v.version, 0) AS version
                    FROM knowledge_sources s
                    LEFT JOIN document_versions v
                      ON v.source_id = s.id AND v.version = s.current_version AND v.status = 'indexed'
                    WHERE {self._read_predicate(not include_unapproved)}
                    ORDER BY s.updated_at DESC, s.id
                    LIMIT %(limit)s""",
                {"tenant_id": scope.tenant_id, "user_id": scope.user_id, "section_id": scope.meeting_id,
                 "limit": max(1, min(limit, 200))})
            return [_document(row) for row in await cur.fetchall()]

    async def delete(self, scope: MemoryScope, document_id: str, *, hard: bool = False) -> bool:
        pool = await self._ensure()
        async with pool.connection() as conn:
            cur = await conn.execute(
                """SELECT id FROM knowledge_sources
                   WHERE id=%s AND tenant_id=%s AND owner_user_id=%s FOR UPDATE""",
                (document_id, scope.tenant_id, scope.user_id))
            if await cur.fetchone() is None:
                return False
            if hard:
                await conn.execute(
                    "DELETE FROM knowledge_sources WHERE id=%s AND tenant_id=%s AND owner_user_id=%s",
                    (document_id, scope.tenant_id, scope.user_id))
            else:
                await conn.execute(
                    """UPDATE knowledge_sources SET deleted_at=%s, updated_at=%s
                       WHERE id=%s AND tenant_id=%s AND owner_user_id=%s AND deleted_at IS NULL""",
                    (utcnow(), utcnow(), document_id, scope.tenant_id, scope.user_id))
            return True

    async def purge_deleted(self, scope: MemoryScope) -> int:
        pool = await self._ensure()
        async with pool.connection() as conn:
            cur = await conn.execute(
                """DELETE FROM knowledge_sources
                   WHERE tenant_id=%s AND owner_user_id=%s AND deleted_at IS NOT NULL""",
                (scope.tenant_id, scope.user_id))
            return int(cur.rowcount or 0)

    async def set_approved(self, scope: MemoryScope, document_id: str, approved: bool) -> KnowledgeDocument:
        pool = await self._ensure()
        async with pool.connection() as conn:
            cur = await conn.execute(
                """UPDATE knowledge_sources SET approved=%s, updated_at=%s
                   WHERE id=%s AND tenant_id=%s AND owner_user_id=%s""",
                (approved, utcnow(), document_id, scope.tenant_id, scope.user_id))
            if cur.rowcount != 1:
                raise KeyError("document not found")
            found = await self._fetch_document(conn, scope, document_id)
            if found is None:  # pragma: no cover
                raise KeyError("document not found")
            return found

    async def grant(self, scope: MemoryScope, document_id: str, subject_id: str,
                    permission: str = "read") -> None:
        if permission != "read":
            raise ValueError("only read grants are supported")
        pool = await self._ensure()
        async with pool.connection() as conn:
            cur = await conn.execute(
                """SELECT 1 FROM knowledge_sources
                   WHERE id=%s AND tenant_id=%s AND owner_user_id=%s""",
                (document_id, scope.tenant_id, scope.user_id))
            if await cur.fetchone() is None:
                raise KeyError("document not found")
            await conn.execute(
                """INSERT INTO acl_grants
                       (id, tenant_id, subject_type, subject_id, resource_type, resource_id, permission)
                   VALUES (%s, %s, 'user', %s, 'document', %s, %s)
                   ON CONFLICT DO NOTHING""",
                (new_id(), scope.tenant_id, subject_id, document_id, permission))

    async def revoke(self, scope: MemoryScope, document_id: str, subject_id: str,
                     permission: str = "read") -> None:
        if permission != "read":
            raise ValueError("only read grants are supported")
        pool = await self._ensure()
        async with pool.connection() as conn:
            cur = await conn.execute(
                """SELECT 1 FROM knowledge_sources
                   WHERE id=%s AND tenant_id=%s AND owner_user_id=%s""",
                (document_id, scope.tenant_id, scope.user_id))
            if await cur.fetchone() is None:
                raise KeyError("document not found")
            await conn.execute(
                """DELETE FROM acl_grants
                   WHERE tenant_id=%s AND subject_type='user' AND subject_id=%s
                     AND resource_type='document' AND resource_id=%s AND permission=%s""",
                (scope.tenant_id, subject_id, document_id, permission))

    async def close(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            await pool.close()
