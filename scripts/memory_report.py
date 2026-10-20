"""Read-only report of what Tara has stored. Prints counts and short excerpts, never keys.

    set -a; source .env; set +a; .venv/bin/python scripts/memory_report.py [--meeting NAME] [--show 20]
"""
from __future__ import annotations

import argparse
import os

import psycopg


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--meeting", default="")
    p.add_argument("--show", type=int, default=10, help="how many recent rows to print")
    a = p.parse_args()
    dsn = os.environ.get("TARA_MEMORY_DATABASE_URL", "")
    if not dsn:
        print("TARA_MEMORY_DATABASE_URL is not set")
        return 2
    where, args = "deleted_at IS NULL", []
    if a.meeting:
        where += " AND meeting_id = %s"
        args.append(a.meeting)
    with psycopg.connect(dsn) as c:
        print("by meeting / kind / status / count")
        for r in c.execute(f"SELECT coalesce(meeting_id,'(global)'), kind, status, count(*) FROM memory_records WHERE {where} "
                           "GROUP BY 1,2,3 ORDER BY 1,2,3", args):
            print("  ", *r)
        print(f"\nnewest {a.show}")
        for r in c.execute(f"SELECT coalesce(meeting_id,'(global)'), kind, status, left(body, 110), created_at::timestamp(0) "
                           f"FROM memory_records WHERE {where} ORDER BY created_at DESC LIMIT %s", [*args, a.show]):
            print("  ", *r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
