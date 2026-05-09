"""
Database access layer for PLA Watch.

All SQL lives here. No ORM — keeping it transparent and dependency-light.
Connection uses WAL mode for safe concurrent reads during site generation.
"""

import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

from config import DB_PATH

logger = logging.getLogger(__name__)

# ── Connection ────────────────────────────────────────────────────────────────

@contextmanager
def get_conn() -> Generator[sqlite3.Connection, None, None]:
    """Yield a connection that auto-commits on clean exit, rolls back on error."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Initialization ────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create tables and seed data if they don't exist. Safe to call repeatedly."""
    schema_path = Path(__file__).parent / "schema.sql"
    sql = schema_path.read_text(encoding="utf-8")
    with get_conn() as conn:
        conn.executescript(sql)
    logger.info("Database initialized at %s", DB_PATH)


# ── Source lookup ─────────────────────────────────────────────────────────────

def get_source_id(slug: str) -> Optional[int]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM sources WHERE slug = ?", (slug,)
        ).fetchone()
    return row["id"] if row else None


# ── Deduplication checks ──────────────────────────────────────────────────────

def url_exists(url: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM articles WHERE url = ?", (url,)
        ).fetchone()
    return row is not None


def hash_exists(content_hash: str) -> bool:
    """True if an article with this content hash is already stored."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM articles WHERE content_hash = ?", (content_hash,)
        ).fetchone()
    return row is not None


# ── Scrape run log ────────────────────────────────────────────────────────────

def start_scrape_run() -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO scrape_runs (status) VALUES ('running')"
        )
        return cur.lastrowid


def complete_scrape_run(
    run_id: int,
    articles_scraped: int,
    articles_new: int,
    articles_analyzed: int,
    errors: list[str],
    status: str = "completed",
) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE scrape_runs
               SET completed_at      = datetime('now'),
                   articles_scraped  = ?,
                   articles_new      = ?,
                   articles_analyzed = ?,
                   errors            = ?,
                   status            = ?
             WHERE id = ?
            """,
            (articles_scraped, articles_new, articles_analyzed,
             json.dumps(errors), status, run_id),
        )


# ── Article writes ────────────────────────────────────────────────────────────

def insert_article(article: dict, scrape_run_id: int) -> Optional[int]:
    """
    Insert a new article. Returns the new row id, or None if the URL
    already exists.
    """
    source_id = get_source_id(article["source_slug"])
    if source_id is None:
        logger.error("Unknown source slug: %s", article["source_slug"])
        return None

    try:
        with get_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO articles
                    (url, content_hash, source_id, scrape_run_id,
                     title_original, text_original, published_date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    article["url"],
                    article["content_hash"],
                    source_id,
                    scrape_run_id,
                    article.get("title_original"),
                    article.get("text_original"),
                    article.get("published_date"),
                ),
            )
            return cur.lastrowid
    except sqlite3.IntegrityError:
        logger.debug("Duplicate URL skipped: %s", article["url"])
        return None


def update_relevance(
    article_id: int,
    score: float,
    reasoning: str,
    passed: bool,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE articles
               SET relevance_score     = ?,
                   relevance_reasoning = ?,
                   passed_relevance    = ?
             WHERE id = ?
            """,
            (score, reasoning, int(passed), article_id),
        )


def update_analysis(
    article_id: int,
    title_english: str,
    text_english: str,
    summary_english: str,
    is_significant: bool,
    significance_reasoning: Optional[str],
    categories: list[str],
    model_id: str,
    prompt_version: str,
) -> None:
    """Persist full analysis results and category tags for a single article."""
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE articles
               SET title_english          = ?,
                   text_english           = ?,
                   summary_english        = ?,
                   is_significant         = ?,
                   significance_reasoning = ?,
                   analyzed_at            = datetime('now'),
                   model_id               = ?,
                   prompt_version         = ?
             WHERE id = ?
            """,
            (
                title_english, text_english, summary_english,
                int(is_significant), significance_reasoning,
                model_id, prompt_version, article_id,
            ),
        )
        # Upsert categories via join table
        for slug in categories:
            row = conn.execute(
                "SELECT id FROM categories WHERE slug = ?", (slug,)
            ).fetchone()
            if row:
                conn.execute(
                    "INSERT OR IGNORE INTO article_categories VALUES (?, ?)",
                    (article_id, row["id"]),
                )
            else:
                logger.warning(
                    "Category slug '%s' not in DB — skipping (check schema.sql seed data)",
                    slug,
                )


# ── Queries for pipeline resume ───────────────────────────────────────────────

def get_articles_pending_analysis() -> list[sqlite3.Row]:
    """
    Return articles that passed relevance but haven't been fully analyzed yet.
    Used to resume a pipeline that was interrupted after relevance scoring.
    """
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT id, url, title_original, text_original
              FROM articles
             WHERE passed_relevance = 1
               AND analyzed_at IS NULL
             ORDER BY id
            """
        ).fetchall()


# ── Site-generation bulk fetch ────────────────────────────────────────────────

def get_all_analyzed_articles() -> list[sqlite3.Row]:
    """
    Return every fully analyzed article, newest first, with source info and
    a comma-separated category_slugs column pre-joined.  Used by the site
    generator to avoid N+1 category queries.
    """
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT a.*,
                   s.slug          AS source_slug,
                   s.display_name  AS source_name,
                   s.language      AS source_language,
                   GROUP_CONCAT(c.slug) AS category_slugs
              FROM articles a
              JOIN sources s ON s.id = a.source_id
              LEFT JOIN article_categories ac ON ac.article_id = a.id
              LEFT JOIN categories c ON c.id = ac.category_id
             WHERE a.passed_relevance = 1
               AND a.analyzed_at IS NOT NULL
             GROUP BY a.id
             ORDER BY a.published_date DESC, a.is_significant DESC,
                      a.relevance_score DESC
            """
        ).fetchall()


# ── Aggregate counts ─────────────────────────────────────────────────────────

def get_total_analyzed_count() -> int:
    """Total articles with full analysis in DB (across all runs)."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM articles WHERE analyzed_at IS NOT NULL"
        ).fetchone()[0]


# ── Site-generation queries ───────────────────────────────────────────────────

def get_articles_for_date(date_str: str) -> list[sqlite3.Row]:
    """Return all analyzed articles for a given date, significance-first."""
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT a.*, s.display_name AS source_name, s.language AS source_language
              FROM articles a
              JOIN sources  s ON s.id = a.source_id
             WHERE a.published_date = ?
               AND a.passed_relevance = 1
               AND a.analyzed_at IS NOT NULL
             ORDER BY a.is_significant DESC, a.relevance_score DESC
            """,
            (date_str,),
        ).fetchall()


def get_article_categories(article_id: int) -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.slug
              FROM article_categories ac
              JOIN categories c ON c.id = ac.category_id
             WHERE ac.article_id = ?
            """,
            (article_id,),
        ).fetchall()
    return [r["slug"] for r in rows]


def get_recent_dates(limit: int = 30) -> list[str]:
    """Return the most recent N distinct publication dates with analyzed articles."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT published_date
              FROM articles
             WHERE passed_relevance = 1
               AND analyzed_at IS NOT NULL
               AND published_date IS NOT NULL
             ORDER BY published_date DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [r["published_date"] for r in rows]


def get_articles_for_date_range(start_date: str, end_date: str) -> list[sqlite3.Row]:
    """
    Return all analyzed articles published between start_date and end_date (inclusive).
    Used by the weekly PLA Watch generator. Read-only.
    """
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT a.*,
                   s.slug          AS source_slug,
                   s.display_name  AS source_name,
                   GROUP_CONCAT(c.slug) AS category_slugs
              FROM articles a
              JOIN sources s ON s.id = a.source_id
              LEFT JOIN article_categories ac ON ac.article_id = a.id
              LEFT JOIN categories c ON c.id = ac.category_id
             WHERE a.passed_relevance = 1
               AND a.analyzed_at IS NOT NULL
               AND a.published_date >= ?
               AND a.published_date <= ?
             GROUP BY a.id
             ORDER BY a.published_date DESC, a.is_significant DESC,
                      a.relevance_score DESC
            """,
            (start_date, end_date),
        ).fetchall()
