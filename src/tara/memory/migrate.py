"""Apply the memory schema. Safe to rerun; never prints the database URL.

    .venv/bin/python -m tara.memory.migrate
    TARA_MEMORY_DATABASE_URL=postgresql://... .venv/bin/python -m tara.memory.migrate
"""
from __future__ import annotations

import argparse
import os
from importlib.resources import files


def _sql(name: str) -> str:
    return files("tara.memory").joinpath(f"migrations/{name}").read_text(encoding="utf-8")


def migrate(dsn: str, *, schema: str | None = None) -> None:
    if not dsn.startswith(("postgresql://", "postgres://")):
        raise ValueError("memory database URL must be a postgresql:// or postgres:// URL")
    try:
        import psycopg
        from psycopg import sql
    except ImportError as e:  # pragma: no cover - only without the optional extra
        raise RuntimeError("PostgreSQL memory needs the optional 'memory' extra: pip install -e '.[memory]'") from e
    with psycopg.connect(dsn) as conn:
        if schema:
            conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        conn.execute(_sql("001_memory.sql"))
        conn.execute(_sql("002_meetings.sql"))
        conn.execute(_sql("003_sessions.sql"))
        conn.execute(_sql("004_session_delete.sql"))
        conn.execute(_sql("005_recovery_and_ranking.sql"))
        conn.execute(_sql("006_knowledge.sql"))
        conn.commit()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tara.memory.migrate", description="Create or update Tara memory tables.")
    p.add_argument("--database-url", default=os.environ.get("TARA_MEMORY_DATABASE_URL", ""),
                   help="PostgreSQL URL (default: TARA_MEMORY_DATABASE_URL)")
    p.add_argument("--schema", default=os.environ.get("TARA_MEMORY_SCHEMA", ""),
                   help="optional schema name (useful for tests)")
    args = p.parse_args(argv)
    if not args.database_url:
        print("error: set TARA_MEMORY_DATABASE_URL or pass --database-url", flush=True)
        return 2
    migrate(args.database_url, schema=args.schema or None)
    print("memory schema ready", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
