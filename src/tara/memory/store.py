"""Storage interfaces for persistent memory.

`InMemoryMemoryStore` is for tests and for a single process with no database.
`PostgresMemoryStore` is the durable implementation. It imports `psycopg` lazily so
the default offline test suite does not need the optional `memory` extra.
"""
from __future__ import annotations

import asyncio
import importlib.util
import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from ..core.errors import ConfigError
from .models import MeetingSession, MemoryHit, MemoryItem, MemoryScope, is_expired, normalize_text, tokens, utcnow

_LIKE_SPECIAL = re.compile(r"([%_\\])")


class MemoryStore(Protocol):
    async def add(self, item: MemoryItem) -> None: ...
    async def update(self, item: MemoryItem) -> None: ...
    async def get(self, scope: MemoryScope, memory_id: str) -> MemoryItem | None: ...
    async def find_exact(self, scope: MemoryScope, text: str) -> MemoryItem | None: ...
    async def search(self, scope: MemoryScope, query: str, limit: int = 5) -> list[MemoryHit]: ...
    async def list_recent(self, scope: MemoryScope, limit: int = 20) -> list[MemoryItem]: ...
    async def delete(self, scope: MemoryScope, memory_id: str) -> bool: ...
    async def list_meetings(self, scope: MemoryScope) -> list[dict[str, Any]]: ...
    async def purge_expired(self, scope: MemoryScope) -> int: ...
    async def start_session(self, scope: MemoryScope, session_id: str, started_at: datetime) -> MeetingSession: ...
    async def end_session(self, scope: MemoryScope, session_id: str, ended_at: datetime) -> None: ...
    async def list_sessions(self, scope: MemoryScope) -> list[MeetingSession]: ...
    async def list_session_items(self, scope: MemoryScope, session_id: str, limit: int = 80) -> list[MemoryItem]: ...
    async def delete_session(self, scope: MemoryScope, session_id: str) -> int: ...
    async def close(self) -> None: ...


class InMemoryMemoryStore:
    """Bounded process-local store. Data is lost on restart; never use it as the only copy."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str], MemoryItem] = {}
        self._sessions: dict[tuple[str, str, str], list[MeetingSession]] = {}
        self._deleted_sessions: set[tuple[str, str, str, str]] = set()      # numbers stay used, rows disappear

    async def add(self, item: MemoryItem) -> None:
        key = (item.scope.tenant_id, item.scope.user_id, item.id)
        if key in self._items:
            raise ValueError("memory id already exists")
        self._items[key] = item

    async def update(self, item: MemoryItem) -> None:
        key = (item.scope.tenant_id, item.scope.user_id, item.id)
        if key not in self._items:
            raise KeyError("memory not found")
        self._items[key] = item

    async def get(self, scope: MemoryScope, memory_id: str) -> MemoryItem | None:
        item = self._items.get((scope.tenant_id, scope.user_id, memory_id))
        return None if item is None or is_expired(item) else item

    async def find_exact(self, scope: MemoryScope, text: str) -> MemoryItem | None:
        wanted = normalize_text(text)
        for item in self._owned(scope):
            if normalize_text(item.text) == wanted and not is_expired(item):
                return item
        return None

    async def search(self, scope: MemoryScope, query: str, limit: int = 5) -> list[MemoryHit]:
        q = tokens(query)
        phrase = normalize_text(query)
        ranked: list[MemoryHit] = []
        for item in self._owned(scope):
            if item.status == "superseded" or is_expired(item):
                continue
            overlap = len(q & tokens(item.text))
            if phrase and phrase in normalize_text(item.text):
                overlap += 2
            if overlap:
                ranked.append(MemoryHit(item, float(overlap), "keyword"))
        ranked.sort(key=lambda h: (-h.score, -h.item.updated_at.timestamp(), h.item.id))
        return ranked[: max(1, limit)]

    async def list_recent(self, scope: MemoryScope, limit: int = 20) -> list[MemoryItem]:
        items = sorted((i for i in self._owned(scope) if i.status != "superseded" and not is_expired(i)),
                       key=lambda i: (i.updated_at, i.id), reverse=True)
        return items[: max(1, limit)]

    async def delete(self, scope: MemoryScope, memory_id: str) -> bool:
        return self._items.pop((scope.tenant_id, scope.user_id, memory_id), None) is not None

    async def close(self) -> None:
        return None

    async def list_meetings(self, scope: MemoryScope) -> list[dict[str, Any]]:
        seen: dict[str, dict[str, Any]] = {}
        for (tenant, user, _), item in self._items.items():
            if tenant != scope.tenant_id or user != scope.user_id or not item.meeting_id or is_expired(item):
                continue
            row = seen.setdefault(item.meeting_id, {"meeting_id": item.meeting_id, "items": 0, "last_active": item.updated_at})
            row["items"] += 1
            row["last_active"] = max(row["last_active"], item.updated_at)
        return sorted(seen.values(), key=lambda r: r["last_active"], reverse=True)

    def _section(self, scope: MemoryScope) -> list[MeetingSession]:
        if scope.meeting_id is None:
            raise ValueError("sessions need a section (meeting_id) in the scope")
        return self._sessions.setdefault((scope.tenant_id, scope.user_id, scope.meeting_id), [])

    async def start_session(self, scope: MemoryScope, session_id: str, started_at: datetime) -> MeetingSession:
        rows = self._section(scope)
        for r in rows:
            if r.session_id == session_id:
                return r
        row = MeetingSession(max((r.number for r in rows), default=0) + 1, session_id, started_at)
        rows.append(row)
        return row

    async def end_session(self, scope: MemoryScope, session_id: str, ended_at: datetime) -> None:
        rows = self._section(scope)
        for i, r in enumerate(rows):
            if r.session_id == session_id:
                rows[i] = MeetingSession(r.number, r.session_id, r.started_at, ended_at)

    async def list_sessions(self, scope: MemoryScope) -> list[MeetingSession]:
        return [r for r in self._section(scope)
                if (scope.tenant_id, scope.user_id, str(scope.meeting_id), r.session_id) not in self._deleted_sessions]

    async def delete_session(self, scope: MemoryScope, session_id: str) -> int:
        key = (scope.tenant_id, scope.user_id, str(scope.meeting_id), session_id)
        if not any(r.session_id == session_id for r in self._section(scope)):
            return 0
        self._deleted_sessions.add(key)
        doomed = [k for k, i in self._items.items() if k[0] == scope.tenant_id and k[1] == scope.user_id
                  and i.session_id == session_id and i.meeting_id == scope.meeting_id]
        for k in doomed:
            del self._items[k]
        return len(doomed)

    async def list_session_items(self, scope: MemoryScope, session_id: str, limit: int = 80) -> list[MemoryItem]:
        items = [i for i in self._owned(scope) if i.session_id == session_id and i.status != "superseded" and not is_expired(i)]
        return sorted(items, key=lambda i: (i.created_at, i.id))[: max(1, limit)]

    async def purge_expired(self, scope: MemoryScope) -> int:
        dead = [k for k, i in self._items.items() if k[0] == scope.tenant_id and k[1] == scope.user_id and is_expired(i)]
        for k in dead:
            del self._items[k]
        return len(dead)

    def _owned(self, scope: MemoryScope) -> Iterable[MemoryItem]:
        for (tenant, user, _), item in self._items.items():
            if tenant == scope.tenant_id and user == scope.user_id:
                if scope.meeting_id is None or item.meeting_id in (None, scope.meeting_id):   # no-meeting rows are global
                    yield item


class PostgresMemoryStore:
    """Durable memory in PostgreSQL, using full-text search first (no embeddings in v1).

    The pool is opened lazily on the first async call. Every query filters on both
    `tenant_id` and `user_id`; the caller cannot widen the scope with query text.
    """

    def __init__(self, dsn: str, *, schema: str | None = None, min_size: int = 1, max_size: int = 4,
                 timeout_s: float = 5.0) -> None:
        if not dsn.startswith(("postgresql://", "postgres://")):
            raise ConfigError("memory database URL must be a postgresql:// or postgres:// URL")
        if importlib.util.find_spec("psycopg") is None or importlib.util.find_spec("psycopg_pool") is None:
            raise ConfigError("PostgreSQL memory needs the optional 'memory' extra: pip install -e '.[memory]'")
        self._dsn = dsn
        self._schema = schema
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
            except ImportError as e:  # pragma: no cover - exercised only without the extra
                raise ConfigError("PostgreSQL memory needs the optional 'memory' extra: pip install -e '.[memory]'") from e
            kwargs: dict[str, Any] = {"row_factory": dict_row}
            if self._schema:
                kwargs["options"] = f"-csearch_path={self._schema}"
            pool = AsyncConnectionPool(conninfo=self._dsn, min_size=self._min_size, max_size=self._max_size,
                                       open=False, kwargs=kwargs)
            await pool.open(wait=True, timeout=self._timeout_s)
            self._pool = pool
            return pool

    async def add(self, item: MemoryItem) -> None:
        pool = await self._ensure()
        async with pool.connection() as conn:
            await conn.execute(
                """INSERT INTO memory_records
                   (id, tenant_id, user_id, session_id, meeting_id, kind, body, status, source,
                    created_at, updated_at, expires_at, confidence, supersedes_id, metadata)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (UUID(item.id), item.scope.tenant_id, item.scope.user_id, item.session_id, item.meeting_id,
                 item.kind, item.text, item.status, item.source, item.created_at, item.updated_at,
                 item.expires_at, item.confidence, UUID(item.supersedes_id) if item.supersedes_id else None,
                 _jsonb(item.metadata)))

    async def update(self, item: MemoryItem) -> None:
        pool = await self._ensure()
        async with pool.connection() as conn:
            cur = await conn.execute(
                """UPDATE memory_records
                   SET session_id=%s, meeting_id=%s, kind=%s, body=%s, status=%s, source=%s,
                       updated_at=%s, expires_at=%s, confidence=%s, supersedes_id=%s, metadata=%s
                   WHERE id=%s AND tenant_id=%s AND user_id=%s AND deleted_at IS NULL""",
                (item.session_id, item.meeting_id, item.kind, item.text, item.status, item.source,
                 item.updated_at, item.expires_at, item.confidence,
                 UUID(item.supersedes_id) if item.supersedes_id else None, _jsonb(item.metadata),
                 UUID(item.id), item.scope.tenant_id, item.scope.user_id))
            if cur.rowcount != 1:
                raise KeyError("memory not found")

    async def get(self, scope: MemoryScope, memory_id: str) -> MemoryItem | None:
        pool = await self._ensure()
        async with pool.connection() as conn:
            cur = await conn.execute(
                """SELECT * FROM memory_records
                   WHERE id=%s AND tenant_id=%s AND user_id=%s AND deleted_at IS NULL
                     AND (expires_at IS NULL OR expires_at > now())""",
                (UUID(memory_id), scope.tenant_id, scope.user_id))
            row = await cur.fetchone()
            return _row_to_item(row) if row else None

    async def find_exact(self, scope: MemoryScope, text: str) -> MemoryItem | None:
        pool = await self._ensure()
        async with pool.connection() as conn:
            cur = await conn.execute(
                """SELECT * FROM memory_records
                   WHERE tenant_id=%s AND user_id=%s AND lower(body)=lower(%s) AND deleted_at IS NULL
                     AND (expires_at IS NULL OR expires_at > now())
                     AND (%s::text IS NULL OR meeting_id IS NULL OR meeting_id = %s)
                   ORDER BY updated_at DESC LIMIT 1""",
                (scope.tenant_id, scope.user_id, text.strip(), scope.meeting_id, scope.meeting_id))
            row = await cur.fetchone()
            return _row_to_item(row) if row else None

    async def search(self, scope: MemoryScope, query: str, limit: int = 5) -> list[MemoryHit]:
        q = query.strip()
        if not q:
            items = await self.list_recent(scope, limit)
            return [MemoryHit(i, 0.0, "recent") for i in items]
        pool = await self._ensure()
        async with pool.connection() as conn:
            cur = await conn.execute(
                """SELECT *, ts_rank_cd(search, q) AS rank
                   FROM memory_records, websearch_to_tsquery('english', %s) q
                   WHERE tenant_id=%s AND user_id=%s AND deleted_at IS NULL
                     AND status <> 'superseded'
                     AND (expires_at IS NULL OR expires_at > now())
                     AND (search @@ q OR body ILIKE %s)
                     AND (%s::text IS NULL OR meeting_id IS NULL OR meeting_id = %s)
                   ORDER BY rank DESC, updated_at DESC LIMIT %s""",
                (q, scope.tenant_id, scope.user_id, f"%{_escape_like(q)}%", scope.meeting_id, scope.meeting_id,
                 max(1, limit)))
            rows = await cur.fetchall()
            words = sorted(tokens(q))
            if not rows and len(words) > 1:
                # A spoken question has words the memory never used; match on any content word instead of all of them.
                cur = await conn.execute(
                    """SELECT *, ts_rank_cd(search, q) AS rank
                       FROM memory_records, to_tsquery('english', %s) q
                       WHERE tenant_id=%s AND user_id=%s AND deleted_at IS NULL
                         AND status <> 'superseded'
                         AND (expires_at IS NULL OR expires_at > now())
                         AND search @@ q
                         AND (%s::text IS NULL OR meeting_id IS NULL OR meeting_id = %s)
                       ORDER BY rank DESC, updated_at DESC LIMIT %s""",
                    (" | ".join(words[:8]), scope.tenant_id, scope.user_id, scope.meeting_id, scope.meeting_id,
                     max(1, limit)))
                rows = await cur.fetchall()
        return [MemoryHit(_row_to_item(r), float(r.get("rank") or 0.0), "fts") for r in rows]

    async def list_recent(self, scope: MemoryScope, limit: int = 20) -> list[MemoryItem]:
        pool = await self._ensure()
        async with pool.connection() as conn:
            cur = await conn.execute(
                """SELECT * FROM memory_records
                   WHERE tenant_id=%s AND user_id=%s AND deleted_at IS NULL
                     AND status <> 'superseded'
                     AND (expires_at IS NULL OR expires_at > now())
                     AND (%s::text IS NULL OR meeting_id IS NULL OR meeting_id = %s)
                   ORDER BY updated_at DESC LIMIT %s""",
                (scope.tenant_id, scope.user_id, scope.meeting_id, scope.meeting_id, max(1, limit)))
            return [_row_to_item(r) for r in await cur.fetchall()]

    async def list_meetings(self, scope: MemoryScope) -> list[dict[str, Any]]:
        pool = await self._ensure()
        async with pool.connection() as conn:
            cur = await conn.execute(
                """SELECT meeting_id, count(*) AS items, max(updated_at) AS last_active
                   FROM memory_records
                   WHERE tenant_id=%s AND user_id=%s AND deleted_at IS NULL AND meeting_id IS NOT NULL
                     AND (expires_at IS NULL OR expires_at > now())
                   GROUP BY meeting_id ORDER BY last_active DESC LIMIT 100""",
                (scope.tenant_id, scope.user_id))
            return [dict(r) for r in await cur.fetchall()]

    async def start_session(self, scope: MemoryScope, session_id: str, started_at: datetime) -> MeetingSession:
        if scope.meeting_id is None:
            raise ValueError("sessions need a section (meeting_id) in the scope")
        pool = await self._ensure()
        for _ in range(3):                                   # a concurrent start may take the number first
            try:
                async with pool.connection() as conn:
                    await conn.execute(
                        """INSERT INTO meeting_sessions (tenant_id, user_id, section_id, number, session_id, started_at)
                           SELECT %s, %s, %s, coalesce(max(number), 0) + 1, %s, %s FROM meeting_sessions
                           WHERE tenant_id=%s AND user_id=%s AND section_id=%s
                           ON CONFLICT (tenant_id, user_id, section_id, session_id) DO NOTHING""",
                        (scope.tenant_id, scope.user_id, scope.meeting_id, session_id, started_at,
                         scope.tenant_id, scope.user_id, scope.meeting_id))
                    cur = await conn.execute(
                        """SELECT number, session_id, started_at, ended_at FROM meeting_sessions
                           WHERE tenant_id=%s AND user_id=%s AND section_id=%s AND session_id=%s""",
                        (scope.tenant_id, scope.user_id, scope.meeting_id, session_id))
                    row = await cur.fetchone()
                    if row:
                        return MeetingSession(int(row["number"]), str(row["session_id"]), row["started_at"], row["ended_at"])
            except Exception as e:  # noqa: BLE001 - unique violation from a race: retry
                if "unique" not in str(e).lower():
                    raise
        raise RuntimeError("could not allocate a meeting number")

    async def end_session(self, scope: MemoryScope, session_id: str, ended_at: datetime) -> None:
        pool = await self._ensure()
        async with pool.connection() as conn:
            await conn.execute(
                "UPDATE meeting_sessions SET ended_at=%s WHERE tenant_id=%s AND user_id=%s AND section_id=%s AND session_id=%s",
                (ended_at, scope.tenant_id, scope.user_id, scope.meeting_id, session_id))

    async def list_sessions(self, scope: MemoryScope) -> list[MeetingSession]:
        pool = await self._ensure()
        async with pool.connection() as conn:
            cur = await conn.execute(
                """SELECT number, session_id, started_at, ended_at FROM meeting_sessions
                   WHERE tenant_id=%s AND user_id=%s AND section_id=%s AND deleted_at IS NULL ORDER BY number""",
                (scope.tenant_id, scope.user_id, scope.meeting_id))
            return [MeetingSession(int(r["number"]), str(r["session_id"]), r["started_at"], r["ended_at"])
                    for r in await cur.fetchall()]

    async def delete_session(self, scope: MemoryScope, session_id: str) -> int:
        """Soft delete: the meeting disappears from lists and its records stop being returned; rows stay in the table."""
        pool = await self._ensure()
        async with pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE meeting_sessions SET deleted_at=now() WHERE tenant_id=%s AND user_id=%s AND section_id=%s "
                "AND session_id=%s AND deleted_at IS NULL", (scope.tenant_id, scope.user_id, scope.meeting_id, session_id))
            if cur.rowcount != 1:
                return 0
            cur = await conn.execute(
                "UPDATE memory_records SET deleted_at=now(), updated_at=now() WHERE tenant_id=%s AND user_id=%s "
                "AND meeting_id=%s AND session_id=%s AND deleted_at IS NULL",
                (scope.tenant_id, scope.user_id, scope.meeting_id, session_id))
            return int(cur.rowcount or 0)

    async def list_session_items(self, scope: MemoryScope, session_id: str, limit: int = 80) -> list[MemoryItem]:
        pool = await self._ensure()
        async with pool.connection() as conn:
            cur = await conn.execute(
                """SELECT * FROM memory_records
                   WHERE tenant_id=%s AND user_id=%s AND session_id=%s AND deleted_at IS NULL
                     AND status <> 'superseded' AND (expires_at IS NULL OR expires_at > now())
                     AND (%s::text IS NULL OR meeting_id IS NULL OR meeting_id = %s)
                   ORDER BY created_at LIMIT %s""",
                (scope.tenant_id, scope.user_id, session_id, scope.meeting_id, scope.meeting_id, max(1, limit)))
            return [_row_to_item(r) for r in await cur.fetchall()]

    async def purge_expired(self, scope: MemoryScope) -> int:
        pool = await self._ensure()
        async with pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM memory_records WHERE tenant_id=%s AND user_id=%s AND expires_at IS NOT NULL AND expires_at <= now()",
                (scope.tenant_id, scope.user_id))
            return int(cur.rowcount or 0)

    async def delete(self, scope: MemoryScope, memory_id: str) -> bool:
        pool = await self._ensure()
        async with pool.connection() as conn:
            cur = await conn.execute(
                """UPDATE memory_records SET deleted_at=now(), updated_at=now()
                   WHERE id=%s AND tenant_id=%s AND user_id=%s AND deleted_at IS NULL""",
                (UUID(memory_id), scope.tenant_id, scope.user_id))
            return cur.rowcount == 1

    async def close(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            await pool.close()


def _escape_like(s: str) -> str:
    return _LIKE_SPECIAL.sub(r"\\\1", s)


def _jsonb(value: dict[str, Any]) -> Any:
    from psycopg.types.json import Jsonb
    return Jsonb(value)


def _row_to_item(row: dict[str, Any]) -> MemoryItem:
    created = row.get("created_at") or utcnow()
    updated = row.get("updated_at") or created
    meta = row.get("metadata")
    return MemoryItem(
        id=str(row["id"]),
        scope=MemoryScope(str(row["tenant_id"]), str(row["user_id"])),
        kind=str(row["kind"]),
        text=str(row["body"]),
        source=str(row["source"]),
        session_id=str(row.get("session_id") or ""),
        meeting_id=str(row["meeting_id"]) if row.get("meeting_id") else None,
        status=str(row["status"]),
        created_at=created if isinstance(created, datetime) else utcnow(),
        updated_at=updated if isinstance(updated, datetime) else utcnow(),
        expires_at=row.get("expires_at") if isinstance(row.get("expires_at"), datetime) else None,
        confidence=float(row.get("confidence") or 1.0),
        supersedes_id=str(row["supersedes_id"]) if row.get("supersedes_id") else None,
        metadata={str(k): v for k, v in meta.items()} if isinstance(meta, dict) else {},
    )
