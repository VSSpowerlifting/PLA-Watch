"""
One-shot cleanup for PLA Daily syndicated reposts that slipped into the DB
before title-based dedup existed.

Groups articles by Chinese-title hash, keeps the highest-priority copy from
each group (by source_priority), deletes the rest.

Usage:
    python scripts/cleanup_duplicates.py --dry-run
    python scripts/cleanup_duplicates.py --apply
"""

import argparse
import sqlite3
import sys
from pathlib import Path

# Allow running from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from processing.dedup import source_priority, title_hash


def find_duplicate_groups(conn: sqlite3.Connection) -> list[list[sqlite3.Row]]:
    """Return groups (lists of >1 row) sharing the same normalized title hash."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, url, title_original FROM articles "
        "WHERE title_original IS NOT NULL AND title_original != ''"
    ).fetchall()

    groups: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        h = title_hash(r["title_original"])
        if not h:
            continue
        groups.setdefault(h, []).append(r)

    return [g for g in groups.values() if len(g) > 1]


def rank_group(group: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """Sort so the winner is first: priority desc, then shorter URL."""
    return sorted(
        group,
        key=lambda r: (-source_priority(r["url"] or ""), len(r["url"] or "")),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="pla_watch.db",
                        help="Path to SQLite DB (default: pla_watch.db)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true",
                      help="Report only; do not delete")
    mode.add_argument("--apply", action="store_true",
                      help="Delete duplicate rows")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: database not found: {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")  # honor ON DELETE CASCADE

    try:
        groups = find_duplicate_groups(conn)

        if not groups:
            print("No duplicate title groups found. Nothing to do.")
            return 0

        print(f"Found {len(groups)} duplicate title group(s):\n")
        to_delete: list[int] = []
        for i, g in enumerate(rank_group(grp) for grp in groups):
            keeper = g[0]
            losers = g[1:]
            print(f"── Group {i+1} ──")
            print(f"  KEEP  id={keeper['id']:<5} "
                  f"prio={source_priority(keeper['url'] or ''):<3} "
                  f"url={keeper['url']}")
            print(f"        title={keeper['title_original']}")
            for r in losers:
                print(f"  DROP  id={r['id']:<5} "
                      f"prio={source_priority(r['url'] or ''):<3} "
                      f"url={r['url']}")
                print(f"        title={r['title_original']}")
                to_delete.append(r["id"])
            print()

        print(f"Summary: {len(to_delete)} row(s) would be deleted.")

        if args.dry_run:
            print("Dry run — no changes made.")
            return 0

        # Apply
        # article_categories has ON DELETE CASCADE on article_id, so a
        # single DELETE on articles suffices.
        conn.executemany(
            "DELETE FROM articles WHERE id = ?",
            [(aid,) for aid in to_delete],
        )
        conn.commit()
        print(f"Deleted {len(to_delete)} row(s).")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
