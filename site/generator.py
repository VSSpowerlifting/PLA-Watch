"""
PLA Watch — static site generator.

Reads all analyzed articles from the SQLite database and renders three page
types to the output/ directory:

  output/index.html          Daily brief for the most recent date with data
  output/archive.html        Searchable archive (client-side JS filtering)
  output/article/{id}.html   Individual article with full translation

Run standalone:
    python site/generator.py

Called from pipeline.py after each analysis run.
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

# Allow running as a standalone script from the project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config import OUTPUT_DIR
from storage.db import get_all_analyzed_articles

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

CATEGORY_LABELS: dict[str, str] = {
    "taiwan":             "Taiwan",
    "south_china_sea":    "South China Sea",
    "east_china_sea":     "East China Sea",
    "us_china_military":  "U.S.–China Military",
    "exercises":          "Exercises",
    "modernization":      "Modernization",
    "doctrine":           "Doctrine",
    "personnel":          "Personnel",
    "nuclear":            "Nuclear",
    "cyber_info":         "Cyber & Info",
    "internal_security":  "Internal Security",
    "coast_guard":        "Coast Guard",
    "military_diplomacy": "Military Diplomacy",
    "political_work":     "Political Work",
}

# Lower number = higher priority; used to sort articles by category
_CATEGORY_PRIORITY: dict[str, int] = {
    "taiwan":             1,
    "south_china_sea":    2,
    "east_china_sea":     3,
    "us_china_military":  4,
    "nuclear":            5,
    "exercises":          6,
    "modernization":      7,
    "coast_guard":        8,
    "military_diplomacy": 9,
    "doctrine":           10,
    "cyber_info":         11,
    "political_work":     12,
    "personnel":          13,
    "internal_security":  14,
}


def _format_date(date_str: str) -> str:
    """'2026-05-07' → '7 May 2026'"""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return d.strftime("%-d %B %Y")
    except (ValueError, AttributeError):
        return date_str or ""


def _article_sort_key(article: dict) -> tuple:
    """Sort: significant first, then by category priority, then by relevance desc."""
    sig = 0 if article["is_significant"] else 1
    cats = article["categories"]
    cat_priority = min((_CATEGORY_PRIORITY.get(c, 99) for c in cats), default=99)
    relevance = -(article["relevance_score"] or 0.0)
    return (sig, cat_priority, relevance)


def _row_to_dict(row) -> dict:
    """Convert a sqlite3.Row to a plain dict and parse categories."""
    d = dict(row)
    slugs = d.pop("category_slugs", "") or ""
    d["categories"] = [s for s in slugs.split(",") if s]
    d["category_labels"] = [CATEGORY_LABELS.get(s, s) for s in d["categories"]]
    d["published_date_fmt"] = _format_date(d.get("published_date", ""))
    return d


# ── Jinja2 setup ──────────────────────────────────────────────────────────────

def _make_env() -> Environment:
    templates_dir = Path(__file__).parent / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=True,
    )
    env.filters["format_date"] = _format_date
    return env


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_site(output_dir: Path = OUTPUT_DIR) -> None:
    """Render the full site to output_dir."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    article_dir = output_dir / "article"
    article_dir.mkdir(exist_ok=True)
    data_dir = output_dir / "data"
    data_dir.mkdir(exist_ok=True)

    env = _make_env()
    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # ── Load all articles ─────────────────────────────────────────────────────
    rows = get_all_analyzed_articles()
    articles = [_row_to_dict(r) for r in rows]

    if not articles:
        logger.warning("No analyzed articles in DB — site will be empty.")

    # Group by date
    by_date: dict[str, list[dict]] = {}
    for a in articles:
        d = a.get("published_date") or "unknown"
        by_date.setdefault(d, []).append(a)

    dates_sorted = sorted(by_date.keys(), reverse=True)

    # Sort each day's articles
    for d in dates_sorted:
        by_date[d].sort(key=_article_sort_key)

    # ── index.html ────────────────────────────────────────────────────────────
    brief_date = dates_sorted[0] if dates_sorted else None
    brief_articles = by_date.get(brief_date, []) if brief_date else []
    n_significant = sum(1 for a in brief_articles if a["is_significant"])

    # Count unique sources for the brief
    brief_sources = len({a["source_slug"] for a in brief_articles})

    tmpl_index = env.get_template("index.html")
    (output_dir / "index.html").write_text(
        tmpl_index.render(
            root_path="",
            brief_date=brief_date,
            brief_date_fmt=_format_date(brief_date) if brief_date else "",
            articles=brief_articles,
            n_significant=n_significant,
            n_sources=brief_sources,
            generated_at=generated_at,
        ),
        encoding="utf-8",
    )
    logger.info("Wrote index.html (%d articles)", len(brief_articles))

    # ── archive.html + data/articles.json ────────────────────────────────────
    archive_json = [
        {
            "id":                a["id"],
            "title_en":         a.get("title_english") or "",
            "title_zh":         a.get("title_original") or "",
            "summary":          a.get("summary_english") or "",
            "date":             a.get("published_date") or "",
            "date_fmt":         a["published_date_fmt"],
            "source":           a.get("source_name") or "",
            "source_slug":      a.get("source_slug") or "",
            "categories":       a["categories"],
            "category_labels":  a["category_labels"],
            "is_significant":   bool(a.get("is_significant")),
            "significance_reason": a.get("significance_reasoning") or "",
            "relevance_score":  round(a.get("relevance_score") or 0.0, 2),
            "url":              a.get("url") or "",
            "article_path":     f"article/{a['id']}.html",
        }
        for a in articles
    ]

    (data_dir / "articles.json").write_text(
        json.dumps(archive_json, ensure_ascii=False, indent=None),
        encoding="utf-8",
    )

    tmpl_archive = env.get_template("archive.html")
    (output_dir / "archive.html").write_text(
        tmpl_archive.render(
            root_path="",
            total_articles=len(articles),
            articles_json=json.dumps(archive_json, ensure_ascii=False),
            category_labels=CATEGORY_LABELS,
            generated_at=generated_at,
        ),
        encoding="utf-8",
    )
    logger.info("Wrote archive.html (%d articles in JSON)", len(articles))

    # ── article/{id}.html ─────────────────────────────────────────────────────
    tmpl_article = env.get_template("article.html")
    count = 0
    for a in articles:
        (article_dir / f"{a['id']}.html").write_text(
            tmpl_article.render(
                root_path="../",
                article=a,
                generated_at=generated_at,
            ),
            encoding="utf-8",
        )
        count += 1

    logger.info("Wrote %d article pages", count)
    logger.info("Site generated → %s", output_dir)


# ── Standalone entry ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )
    generate_site()
    print(f"\nSite written to: {OUTPUT_DIR}/")
    print(f"Open with:  open {OUTPUT_DIR}/index.html\n")
