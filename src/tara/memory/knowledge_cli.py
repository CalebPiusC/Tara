"""`tara knowledge`: ingest, inspect, share and delete approved documents.

    tara knowledge add ./handbook.md --section onboarding
    tara knowledge list
    tara knowledge search "refund window"
    tara knowledge approve <document-id>
    tara knowledge share <document-id> alice
    tara knowledge delete <document-id> --hard
    tara knowledge purge --yes

The database is the same PostgreSQL database as long-term memory. Run
`tara migrate` first. No command prints the database URL.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date

from ..core.errors import ConfigError, PermissionDenied
from .knowledge import KnowledgeService
from .knowledge_store import PostgresKnowledgeStore
from .models import MemoryScope


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--database-url", default=os.environ.get("TARA_MEMORY_DATABASE_URL", ""),
                        help="PostgreSQL URL (default: TARA_MEMORY_DATABASE_URL)")
    parser.add_argument("--schema", default=os.environ.get("TARA_MEMORY_SCHEMA", ""),
                        help="optional schema name (useful for tests)")
    parser.add_argument("--tenant", default=os.environ.get("TARA_MEMORY_TENANT", "local"),
                        help="tenant id (default: TARA_MEMORY_TENANT or local)")
    parser.add_argument("--user", default=os.environ.get("TARA_MEMORY_USER", "local-user"),
                        help="owner user id (default: TARA_MEMORY_USER or local-user)")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tara knowledge", description="Manage approved documents for retrieval.")
    subs = parser.add_subparsers(dest="command")

    add = subs.add_parser("add", help="ingest a .txt or .md file")
    _common(add)
    add.add_argument("path")
    add.add_argument("--title", default="", help="document title (default: the file name)")
    add.add_argument("--source", default="", help="stable source name (default: the resolved path)")
    add.add_argument("--section", default="", help="make it visible only in this section")
    add.add_argument("--draft", action="store_true", help="ingest unapproved; retrieval ignores it until approved")
    add.add_argument("--effective-date", default="", help="YYYY-MM-DD, shown as the document date")

    listing = subs.add_parser("list", help="list readable documents")
    _common(listing)
    listing.add_argument("--limit", type=int, default=50)
    listing.add_argument("--drafts", action="store_true", help="include your own unapproved documents")

    search = subs.add_parser("search", help="search approved documents")
    _common(search)
    search.add_argument("query")
    search.add_argument("--section", default="", help="also include this section's documents")
    search.add_argument("--limit", type=int, default=4)
    search.add_argument("--chars", type=int, default=2500)

    show = subs.add_parser("show", help="show one document's metadata")
    _common(show)
    show.add_argument("document_id")

    approve = subs.add_parser("approve", help="approve or unapprove one of your documents")
    _common(approve)
    approve.add_argument("document_id")
    approve.add_argument("--unapprove", action="store_true")

    share = subs.add_parser("share", help="grant another user read access")
    _common(share)
    share.add_argument("document_id")
    share.add_argument("subject")

    revoke = subs.add_parser("revoke", help="revoke another user's read access")
    _common(revoke)
    revoke.add_argument("document_id")
    revoke.add_argument("subject")

    delete = subs.add_parser("delete", help="soft-delete a document; --hard deletes it now")
    _common(delete)
    delete.add_argument("document_id")
    delete.add_argument("--hard", action="store_true")

    purge = subs.add_parser("purge", help="permanently remove your soft-deleted documents")
    _common(purge)
    purge.add_argument("--yes", action="store_true", help="required confirmation")
    return parser


def _scope(args: argparse.Namespace) -> MemoryScope:
    section = getattr(args, "section", "").strip() or None
    return MemoryScope(args.tenant, args.user, meeting_id=section)


def _service(args: argparse.Namespace) -> KnowledgeService:
    if not args.database_url:
        raise ConfigError("set TARA_MEMORY_DATABASE_URL or pass --database-url")
    store = PostgresKnowledgeStore(args.database_url, schema=args.schema or None)
    return KnowledgeService(store)


async def _run(args: argparse.Namespace) -> int:
    service = _service(args)
    try:
        if args.command == "add":
            effective = date.fromisoformat(args.effective_date) if args.effective_date else None
            document = await service.ingest_file(
                _scope(args), args.path, title=args.title, source=args.source,
                section_id=args.section.strip() or None, approved=not args.draft, effective_date=effective)
            print(f"added {document.id} v{document.version} approved={'yes' if document.approved else 'no'} "
                  f"{document.title}")
            return 0
        if args.command == "list":
            documents = await service.list_documents(_scope(args), limit=args.limit,
                                                      include_unapproved=args.drafts)
            if not documents:
                print("no documents")
                return 0
            for document in documents:
                section = document.section_id or "all sections"
                print(f"{document.id}  v{document.version}  approved={'yes' if document.approved else 'no'}  "
                      f"{section}  {document.title}  ({document.source})")
            return 0
        if args.command == "search":
            hits = await service.search(_scope(args), args.query, limit=args.limit, max_chars=args.chars)
            print(f"{len(hits)} passage(s)")
            for hit in hits:
                print(f"\n[{hit.chunk.id}] {hit.citation} (score {hit.score:.3f})")
                print(hit.chunk.render())
            return 0
        if args.command == "show":
            found = await service.get(_scope(args), args.document_id, approved_only=False)
            if found is None:
                print("document not found")
                return 1
            print(f"id: {found.id}\ntitle: {found.title}\nsource: {found.source}\n"
                  f"version: {found.version}\napproved: {'yes' if found.approved else 'no'}\n"
                  f"section: {found.section_id or 'all sections'}\n"
                  f"effective_date: {found.effective_date.isoformat() if found.effective_date else '-'}\n"
                  f"updated_at: {found.updated_at.isoformat()}")
            return 0
        if args.command == "approve":
            document = await service.set_approved(_scope(args), args.document_id, not args.unapprove)
            print(f"{document.id} approved={'yes' if document.approved else 'no'}")
            return 0
        if args.command == "share":
            await service.grant(_scope(args), args.document_id, args.subject)
            print(f"shared {args.document_id} with {args.subject}")
            return 0
        if args.command == "revoke":
            await service.revoke(_scope(args), args.document_id, args.subject)
            print(f"revoked {args.subject} from {args.document_id}")
            return 0
        if args.command == "delete":
            deleted = await service.delete(_scope(args), args.document_id, hard=args.hard)
            print("deleted" if deleted else "document not found")
            return 0 if deleted else 1
        if args.command == "purge":
            if not args.yes:
                print("refusing to purge without --yes", file=sys.stderr)
                return 2
            count = await service.purge_deleted(_scope(args))
            print(f"purged {count} document(s)")
            return 0
        return 2
    finally:
        await service.close()


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    try:
        return asyncio.run(_run(args))
    except asyncio.CancelledError:
        raise
    except (ConfigError, PermissionDenied, ValueError, KeyError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary; never print a DSN or a traceback
        print(f"error: document operation failed ({type(exc).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
