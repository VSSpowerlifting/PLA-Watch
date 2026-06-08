#!/usr/bin/env python3
"""
Pre-deploy validation for the rendered site in output/.

Run after site generation and before any GitHub Pages deploy. A non-zero exit
blocks deployment, so a broken render never reaches production. Stdlib-only so
it runs in workflows without installing project dependencies.

Checks (each failure is fatal unless noted):
  1. output/index.html exists and is non-empty.
  2. No unrendered Jinja markers ({{ , {% , %}) remain in any .html file.
  3. output/data/articles.json exists and parses as a JSON list.
  4. Every article_path referenced in articles.json exists on disk.
  5. Every non-empty `date` is a real YYYY-MM-DD (empty date → warning only).
  6. No analyzed article has a blank summary.

Usage:
    python3 scripts/validate_output.py [output_dir]   # default: ../output
"""

import json
import re
import sys
from datetime import date
from pathlib import Path

# Jinja markers that indicate an unrendered template. Deliberately excludes the
# bare "}}" because minified CSS media queries legitimately end in "}}"; any
# unrendered expression still contains "{{", so detection stays complete.
JINJA_MARKER = re.compile(r"\{\{|\{%|%\}")
DATE_RE      = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _resolve_output_dir(argv: list[str]) -> Path:
    if len(argv) > 1:
        return Path(argv[1]).resolve()
    return (Path(__file__).resolve().parent.parent / "output").resolve()


def validate(output_dir: Path) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). A non-empty errors list must block deploy."""
    errors:   list[str] = []
    warnings: list[str] = []

    if not output_dir.is_dir():
        return ([f"output directory does not exist: {output_dir}"], warnings)

    # 1. index.html present and non-empty
    index = output_dir / "index.html"
    if not index.is_file():
        errors.append("output/index.html is missing")
    elif index.stat().st_size == 0:
        errors.append("output/index.html is empty")

    # 2. No unrendered Jinja markers in any rendered HTML file
    for html in sorted(output_dir.rglob("*.html")):
        try:
            text = html.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"could not read {html.relative_to(output_dir)}: {exc}")
            continue
        m = JINJA_MARKER.search(text)
        if m:
            line = text.count("\n", 0, m.start()) + 1
            errors.append(
                f"unrendered Jinja marker {m.group()!r} in "
                f"{html.relative_to(output_dir)}:{line}"
            )

    # 3. articles.json present and parses as a list
    data_file = output_dir / "data" / "articles.json"
    if not data_file.is_file():
        errors.append("output/data/articles.json is missing")
        return (errors, warnings)
    try:
        articles = json.loads(data_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        errors.append(f"output/data/articles.json does not parse: {exc}")
        return (errors, warnings)
    if not isinstance(articles, list):
        errors.append("output/data/articles.json is not a JSON list")
        return (errors, warnings)

    # 4–6. Per-article integrity
    missing_pages: list[str] = []
    bad_dates:     list[str] = []
    blank_dates:   list[str] = []
    blank_summary: list[str] = []

    for entry in articles:
        aid = entry.get("id", "?")

        rel = entry.get("article_path", "")
        if not rel or not (output_dir / rel).is_file():
            missing_pages.append(f"id={aid} → {rel or '(no article_path)'}")

        d = (entry.get("date") or "").strip()
        if not d:
            blank_dates.append(str(aid))
        elif not DATE_RE.match(d) or not _is_real_date(d):
            bad_dates.append(f"id={aid} → {d!r}")

        if not (entry.get("summary") or "").strip():
            blank_summary.append(str(aid))

    if missing_pages:
        errors.append(
            f"{len(missing_pages)} article page(s) referenced but missing: "
            + ", ".join(missing_pages[:10])
            + (" …" if len(missing_pages) > 10 else "")
        )
    if bad_dates:
        errors.append(
            f"{len(bad_dates)} article(s) with malformed date: "
            + ", ".join(bad_dates[:10])
            + (" …" if len(bad_dates) > 10 else "")
        )
    if blank_summary:
        errors.append(
            f"{len(blank_summary)} analyzed article(s) with a blank summary: ids "
            + ", ".join(blank_summary[:20])
            + (" …" if len(blank_summary) > 20 else "")
        )
    if blank_dates:
        warnings.append(
            f"{len(blank_dates)} article(s) with an empty date: ids "
            + ", ".join(blank_dates[:20])
            + (" …" if len(blank_dates) > 20 else "")
        )

    return (errors, warnings)


def _is_real_date(s: str) -> bool:
    try:
        date.fromisoformat(s)
        return True
    except ValueError:
        return False


def main() -> int:
    output_dir = _resolve_output_dir(sys.argv)
    errors, warnings = validate(output_dir)

    for w in warnings:
        print(f"WARN:  {w}")
    for e in errors:
        print(f"ERROR: {e}")

    if errors:
        print(f"\nValidation FAILED — {len(errors)} error(s). Deploy blocked.")
        return 1
    print(f"Validation passed ({len(warnings)} warning(s)). Output OK: {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
