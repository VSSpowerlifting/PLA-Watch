"""
Weekly PLA Watch edition generator.

Usage:
    python scripts/generate_pla_watch.py [--week-ending YYYY-MM-DD] [--dry-run]

Reads analyzed articles from the DB for the given week, calls the Claude API
with structured tool_use output, and writes HTML + LinkedIn .txt files.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

# Make project root importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import anthropic
from jinja2 import FileSystemLoader, Environment

from config import DB_PATH, ANTHROPIC_API_KEY
from storage.db import get_articles_for_date_range


# ── Author identity ──────────────────────────────────────────────────────────

AUTHOR_NAME = "Benjamin Yang"
AUTHOR_TITLE = "Founder & Principal Analyst, China Mil Watch"
AUTHOR_BIO = (
    "Benjamin Yang is the founder of China Mil Watch and an incoming "
    "International Affairs student at George Washington University’s "
    "Elliott School, focused on U.S.-China relations, public diplomacy, "
    "and security affairs."
)
AUTHOR_LINKS = {
    "LinkedIn":        "https://www.linkedin.com/in/benjamin-yang-42b525294",
    "Email":           "mailto:ben.yang@gwmail.gwu.edu",
    "China Mil Watch": "../../index.html",
}


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="Generate weekly PLA Watch edition")
    parser.add_argument(
        "--week-ending",
        metavar="YYYY-MM-DD",
        help="Sunday end date for the edition (default: Sunday of current week)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated content to stdout; do not write files",
    )
    return parser.parse_args()


def resolve_week_ending(raw: Optional[str]) -> date:
    if raw:
        return date.fromisoformat(raw)
    today = date.today()
    # Roll forward to the next Sunday (weekday 6)
    days_until_sunday = (6 - today.weekday()) % 7
    return today + timedelta(days=days_until_sunday)


# ── Style guide extract ───────────────────────────────────────────────────────

STYLE_EXTRACT = """
VOICE & TONE RULES
- Serious, human, direct, analytical but not overconfident. Concrete before abstract.
- Source-grounded. Willing to say when something is routine.
- Feel: "Here's what stood out. Here's why it may matter. Here's what was routine."

OPENING NOTE
Good: "The part that stood out this week was not the volume of Taiwan coverage, but how routine it felt."
Good: "This was a quiet week in Chinese military media, which is not the same as an empty one."
Avoid: "In today's complex geopolitical landscape…" / "As tensions continue to rise…"

PARAGRAPH STRUCTURE (preferred)
1. Concrete source detail.
2. Plain-English explanation.
3. Analytical interpretation.
4. Limiting sentence.

ANALYTICAL VERBS (use these)
suggests / signals / frames / emphasizes / points to / fits a pattern / makes visible /
should be read as / helps explain / does not by itself prove

OVERCLAIMING VERBS (avoid)
proves / confirms / exposes / reveals the truth / marks a turning point / changes everything

ANTI-OVERCLAIMING RULE (strict)
Do not use superlatives such as "possibly ever," "most consequential," "unprecedented,"
"historic," "largest," "first," "by any measure," or "major turning point" unless the
article data EXPLICITLY supports them. The China Mil Watch corpus is recent and partial —
it almost never supports historical-primacy claims. When in doubt, weaken.

Era and period comparisons — "in the Xi era," "in the post-2012 period," "since the 2015
reforms," "in the reform era" — are allowed ONLY when the article data directly supports
them (e.g., the article itself uses that frame, or prior editions establish the baseline).
Otherwise use safer language: "unusually significant" / "notable" / "stands out in this
week's coverage" / "one of the clearest signals this week."

Prefer: "unusually significant" / "notable" / "one of the clearest signals this week" /
"a consequential public disciplinary signal" / "stands out in this week's coverage."
A reader can extend a careful claim. They cannot retract a confident one.

SIGNIFICANCE HANDLING
Do not manufacture importance. If the week is routine, say so.
Acceptable: "The signal this week was limited." / "Most coverage followed familiar patterns."
When something IS significant, explain using: unusual placement, senior personnel,
PLA discipline/corruption connection, repeated terminology, source hierarchy.

THIN-WEEK RULE
If the dataset spans fewer than 4 distinct days, frame the edition as limited or early — not
as a full mature weekly readout. Note in the opening that the dataset is partial. Do not
write "everything else was quiet" or "the rest of the week was routine" when only 1-2 days
are present, because most of the week was not observed. Use "in the days observed" or
"in the data available this week." The title and dek must not promise a full weekly summary.

AVOID
clickbait / "breaking news" / fake certainty / "game-changing" / "shocking" /
"complex geopolitical landscape" / "this development underscores" / "it is important to note" /
abstract noun pileups / dramatic conclusions unsupported by the source

SENTENCE RHYTHM
Vary sentence length. Longer analytical sentences should often be followed by a shorter one.
Good: "Most of this week's coverage was routine. That does not make it useless."

CONCRETE ACTORS
Always tie abstract claims to visible actors: PLA Daily, Central Military Commission,
Ministry of National Defense, Eastern Theater Command, Southern Theater Command,
Chinese defense ministry spokespeople, PRC state media, military academies, units, officers.

RELATIONSHIP TO EVIDENCE
Stay close to the source material. Do not invent quotes, statistics, causal claims,
military movements, official positions, or claims unsupported by input.
If input is thin, write a thinner and more cautious post.

OFFICIAL MEDIA FRAMING
Use official media as evidence of messaging and framing — not as a transparent record of reality.
"The article says…" / "The framing suggests…" / "This does not prove…"

WEEKLY EDITION MUST ANSWER
1. What did Chinese military media emphasize this week?
2. What stood out?
3. Why might it matter?
4. What was routine?
5. What should readers watch next?
6. What should readers avoid overreading?
"""


# ── Article formatting ────────────────────────────────────────────────────────

def format_articles_for_prompt(articles) -> str:
    lines = []
    for i, a in enumerate(articles, 1):
        cats = (a["category_slugs"] or "").replace(",", ", ")
        sig_flag = "YES" if a["is_significant"] else "no"
        lines.append(
            f"[{i}] {a['published_date']} | {a['source_name']} | significant={sig_flag} | score={a['relevance_score']:.2f}\n"
            f"    Title: {a['title_english']}\n"
            f"    Categories: {cats or 'none'}\n"
            f"    Summary: {a['summary_english'] or '(no summary)'}\n"
            f"    Significance: {a['significance_reasoning'] or '—'}\n"
            f"    URL: {a['url']}\n"
        )
    return "\n".join(lines)


def compute_stats(articles) -> dict:
    cat_counter: Counter = Counter()
    sources_seen = set()
    dates_seen = set()
    n_significant = 0

    for a in articles:
        if a["is_significant"]:
            n_significant += 1
        sources_seen.add(a["source_name"])
        if a["published_date"]:
            dates_seen.add(a["published_date"])
        for slug in (a["category_slugs"] or "").split(","):
            slug = slug.strip()
            if slug:
                cat_counter[slug] += 1

    return {
        "total_articles": len(articles),
        "n_significant": n_significant,
        "categories_seen": [cat for cat, _ in cat_counter.most_common()],
        "dates_covered": sorted(dates_seen),
        "sources_seen": sorted(sources_seen),
    }


# ── Claude API call ───────────────────────────────────────────────────────────

TOOL_SCHEMA = {
    "name": "compose_pla_watch_edition",
    "description": "Compose a complete weekly PLA Watch newsletter edition as structured fields.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": 'Newsletter title: "The PLA Watch: [specific theme from this week]"',
            },
            "dek": {
                "type": "string",
                "description": "1-2 sentence subtitle summarizing the edition.",
            },
            "signal": {
                "type": "string",
                "description": (
                    "One short sentence (≤ 28 words) capturing this week's single signal — the one line "
                    "you would tell a busy reader if they only had ten seconds. Optional but strongly preferred. "
                    "Restrained, source-grounded, no superlatives. If the week is genuinely too thin to have a "
                    "single signal, return an empty string and the template will omit the card."
                ),
            },
            "opening_note": {
                "type": "string",
                "description": "2-4 paragraphs. Human, direct opening. Paragraphs separated by double newline.",
            },
            "what_stood_out": {
                "type": "string",
                "description": "The most important article, theme, or pattern from the week.",
            },
            "why_it_matters": {
                "type": "string",
                "description": "Plain-English explanation of institutional or strategic significance.",
            },
            "what_was_routine": {
                "type": "string",
                "description": "What appeared normal, repetitive, or not worth overreading.",
            },
            "term_to_know_term": {
                "type": "string",
                "description": "One PLA/PRC/Chinese military-media term or institution.",
            },
            "term_to_know_explanation": {
                "type": "string",
                "description": "Clear plain-English explanation of the term.",
            },
            "what_im_watching_next": {
                "type": "string",
                "description": "Forward-looking but cautious section.",
            },
            "edition_type": {
                "type": "string",
                "enum": ["significant", "routine"],
                "description": "Model's assessment of whether this was a significant or routine week.",
            },
            "linkedin_version": {
                "type": "string",
                "description": (
                    "Full LinkedIn-formatted post. Strong but restrained opening. "
                    "Section headings with ##. No footnotes. Article URLs listed at "
                    "bottom under '## Source trail'. End with: "
                    "'I welcome comments or corrections from people working on Chinese military media, "
                    "PLA studies, or U.S.-China security.'"
                ),
            },
        },
        "required": [
            "title", "dek", "opening_note", "what_stood_out", "why_it_matters",
            "what_was_routine", "term_to_know_term", "term_to_know_explanation",
            "what_im_watching_next", "edition_type", "linkedin_version",
        ],
    },
}

SYSTEM_PROMPT = (
    "You are the editorial engine for The PLA Watch, a reader-facing weekly newsletter "
    "from China Mil Watch. You write source-grounded, analytically restrained foreign policy "
    "analysis of Chinese military media. You do not invent facts, quotes, statistics, or causal "
    "claims. You stay close to the source material. You write in a human, direct, serious voice "
    "— not academic, not hype-driven. You are guided by the style guide provided."
)


def call_claude(article_block: str, stats: dict, week_ending: str, week_start: str, edition_type_hint: str) -> dict:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    stats_block = (
        f"Week: {week_start} to {week_ending}\n"
        f"Total articles: {stats['total_articles']}\n"
        f"Significant articles: {stats['n_significant']}\n"
        f"Dates covered: {', '.join(stats['dates_covered'])}\n"
        f"Sources: {', '.join(stats['sources_seen'])}\n"
        f"Top categories: {', '.join(stats['categories_seen'][:8])}\n"
        f"Edition type hint: {edition_type_hint}\n"
    )

    user_content = [
        {
            "type": "text",
            "text": "STYLE GUIDE EXTRACT:\n\n" + STYLE_EXTRACT,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": (
                f"WEEK SUMMARY STATS:\n\n{stats_block}\n\n"
                f"ARTICLES FOR THIS WEEK:\n\n{article_block}\n\n"
                "INSTRUCTIONS:\n\n"
                "Compose the full weekly PLA Watch edition using the tool. Follow the 9-section structure:\n"
                "1. title — 'The PLA Watch: [theme]'\n"
                "2. dek — 1-2 sentence subtitle\n"
                "3. opening_note — 2-4 paragraphs, human opening\n"
                "4. what_stood_out — most important item or pattern\n"
                "5. why_it_matters — plain-English significance\n"
                "6. what_was_routine — what not to overread\n"
                "7. term_to_know_term + term_to_know_explanation — one PLA/PRC term\n"
                "8. what_im_watching_next — cautious forward look\n"
                "9. edition_type — 'significant' or 'routine' based on the week\n"
                "10. linkedin_version — full LinkedIn post with ## headings, source URLs at bottom\n\n"
                "The LinkedIn version must end with: "
                "'I welcome comments or corrections from people working on Chinese military media, "
                "PLA studies, or U.S.-China security.'\n\n"
                "Stay close to the source material. Do not invent quotes, statistics, or movements. "
                "If the week was quiet, say so. Restrained is better than false drama.\n\n"
                "HARD CONSTRAINTS:\n"
                "- Do not use superlatives like 'possibly ever,' 'most consequential,' 'unprecedented,' "
                "'historic,' 'largest,' 'first,' or 'major turning point' unless the article data directly "
                "supports them. Default to careful framing: 'unusually significant,' 'notable,' "
                "'one of the clearest signals this week,' 'a consequential public disciplinary signal.'\n"
                f"- The current dataset spans {len(stats['dates_covered'])} distinct day(s) of the 7-day window. "
                f"{'Treat this as a thin/early edition. The title and dek must not promise a full weekly readout. ' if len(stats['dates_covered']) < 4 else ''}"
                "Do not write 'the rest of the week was routine' or 'everything else was quiet' if you have not "
                "observed most of the week. Use 'in the days observed' or 'in the data available this week.'\n"
                "- When in doubt, weaken the claim."
            ),
        },
    ]

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        tools=[TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "compose_pla_watch_edition"},
        messages=[{"role": "user", "content": user_content}],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "compose_pla_watch_edition":
            return block.input

    raise ValueError(f"No tool_use block in response. Raw: {response}")


# ── Validation ────────────────────────────────────────────────────────────────

REQUIRED_FIELDS = [
    "title", "dek", "opening_note", "what_stood_out", "why_it_matters",
    "what_was_routine", "term_to_know_term", "term_to_know_explanation",
    "what_im_watching_next", "edition_type", "linkedin_version",
]


def validate_result(result: dict) -> list[str]:
    errors = []
    for field in REQUIRED_FIELDS:
        if not result.get(field):
            errors.append(f"Missing or empty field: {field}")
    if result.get("edition_type") not in ("significant", "routine"):
        errors.append(f"Invalid edition_type: {result.get('edition_type')!r}")
    return errors


# ── Source-trail capping ─────────────────────────────────────────────────────

SOURCE_TRAIL_CAP = 13   # Significant items first; cap total at this many.


def build_source_trail(articles, cap: int = SOURCE_TRAIL_CAP):
    """
    Build the source trail entries for the post template.

    Order: significant items first, then routine items by relevance score.
    Cap at ``cap`` items unless the article count is already smaller.
    Always preserves original source URLs and per-item ``is_significant``.

    Returns a tuple ``(entries, truncated)``.
    """
    sig = [a for a in articles if a["is_significant"]]
    routine = [a for a in articles if not a["is_significant"]]
    routine.sort(key=lambda a: (a["relevance_score"] or 0.0), reverse=True)
    ordered = sig + routine
    truncated = len(ordered) > cap
    chosen = ordered[:cap] if truncated else ordered
    entries = [
        {
            "title":          a["title_english"],
            "url":            a["url"],
            "source":         a["source_name"],
            "date":           a["published_date"],
            "is_significant": bool(a["is_significant"]),
        }
        for a in chosen
    ]
    return entries, truncated


# ── Edition labeling ─────────────────────────────────────────────────────────

def derive_edition_label(edition_type: str, days_covered: int) -> str:
    """
    Compose a short, human-readable edition label combining the model-assigned
    edition_type with a thin-week marker when applicable. Returns a string
    suitable for display in the issue badge / sidebar.
    """
    base = "Significant" if edition_type == "significant" else "Routine"
    if days_covered < 4:
        return f"Thin week · {base.lower()}"
    return base


# ── HTML rendering ────────────────────────────────────────────────────────────

def _build_context(*sources: dict, **extra) -> dict:
    """
    Merge multiple dicts into a single template context, raising on duplicate
    keys instead of letting Jinja's render() fail with a confusing
    "multiple values for keyword argument" TypeError. Use this whenever a
    render call would otherwise unpack two or more dicts together.
    """
    ctx: dict = {}
    for src in sources:
        for k, v in src.items():
            if k in ctx:
                raise ValueError(
                    f"Template context collision on key {k!r}. "
                    f"Rename one of the source keys before rendering."
                )
            ctx[k] = v
    for k, v in extra.items():
        if k in ctx:
            raise ValueError(f"Template context collision on key {k!r}.")
        ctx[k] = v
    return ctx


def render_post(result: dict, meta: dict) -> str:
    env = Environment(loader=FileSystemLoader(str(ROOT / "site" / "templates")))
    template = env.get_template("pla-watch-post.html")
    # result and meta both carry a "title" key (post title vs. sidecar title).
    # Strip layout-only fields out of meta so the post-content keys from
    # `result` win cleanly. _build_context raises on any remaining collision.
    layout_meta = {
        "week_ending":   meta["week_ending"],
        "week_start":    meta["week_start"],
        "n_articles":    meta["n_articles"],
        "n_significant": meta["n_significant"],
        "sources_seen":  meta.get("sources_seen", []),
        "articles":      meta.get("articles", []),
        "days_covered":  meta.get("days_covered", 0),
        "edition_label": meta.get("edition_label", ""),
        "source_trail_truncated": meta.get("source_trail_truncated", False),
        "author_name":   meta.get("author_name", AUTHOR_NAME),
        "author_title":  meta.get("author_title", AUTHOR_TITLE),
        "author_bio":    meta.get("author_bio", AUTHOR_BIO),
        "author_links":  meta.get("author_links", AUTHOR_LINKS),
    }
    context = _build_context(result, layout_meta, root_path="../../")
    return template.render(**context)


def render_index(posts_meta: list[dict]) -> str:
    env = Environment(loader=FileSystemLoader(str(ROOT / "site" / "templates")))
    template = env.get_template("pla-watch-index.html")
    latest = posts_meta[0] if posts_meta else None
    archive = posts_meta[1:] if len(posts_meta) > 1 else []
    return template.render(latest_post=latest, archive_posts=archive, root_path="../")


def render_archive(posts_meta: list[dict]) -> str:
    env = Environment(loader=FileSystemLoader(str(ROOT / "site" / "templates")))
    template = env.get_template("pla-watch-archive.html")
    return template.render(posts=posts_meta, root_path="../")


# ── Sidecar JSON ──────────────────────────────────────────────────────────────

def load_existing_posts(posts_dir: Path) -> list[dict]:
    metas = []
    for json_path in sorted(posts_dir.glob("*.json"), reverse=True):
        try:
            metas.append(json.loads(json_path.read_text(encoding="utf-8")))
        except Exception:
            pass
    return metas


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    week_ending = resolve_week_ending(args.week_ending)
    week_start = week_ending - timedelta(days=6)

    week_ending_str = week_ending.isoformat()
    week_start_str = week_start.isoformat()

    print(f"Fetching articles {week_start_str} → {week_ending_str} …")
    articles = get_articles_for_date_range(week_start_str, week_ending_str)

    if not articles:
        print(f"ERROR: No analyzed articles found for {week_start_str} to {week_ending_str}.")
        sys.exit(1)

    stats = compute_stats(articles)
    distinct_dates = len(stats["dates_covered"])
    edition_type_hint = "thin" if distinct_dates < 3 else "standard"

    print(f"Found {stats['total_articles']} articles across {distinct_dates} dates "
          f"({stats['n_significant']} significant). Edition hint: {edition_type_hint}")

    article_block = format_articles_for_prompt(articles)

    print("Calling Claude API …")
    try:
        result = call_claude(article_block, stats, week_ending_str, week_start_str, edition_type_hint)
    except anthropic.APIError as exc:
        print(f"ERROR: Anthropic API error: {exc}")
        sys.exit(1)

    errors = validate_result(result)
    if errors:
        print("ERROR: Validation failed:")
        for e in errors:
            print(f"  - {e}")
        print("\nRaw result:", json.dumps(result, indent=2, ensure_ascii=False))
        sys.exit(1)

    if args.dry_run:
        print("\n" + "=" * 72)
        print(result["title"])
        print(result["dek"])
        if result.get("signal"):
            print("\n--- THIS WEEK'S SIGNAL ---")
            print(result["signal"])
        print("\n--- OPENING NOTE ---")
        print(result["opening_note"])
        print("\n--- WHAT STOOD OUT ---")
        print(result["what_stood_out"])
        print("\n--- WHY IT MATTERS ---")
        print(result["why_it_matters"])
        print("\n--- WHAT WAS ROUTINE ---")
        print(result["what_was_routine"])
        print(f"\n--- TERM TO KNOW: {result['term_to_know_term']} ---")
        print(result["term_to_know_explanation"])
        print("\n--- WHAT I'M WATCHING NEXT ---")
        print(result["what_im_watching_next"])
        print(f"\nEdition type: {result['edition_type']}")
        print("\n--- LINKEDIN VERSION ---")
        print(result["linkedin_version"])
        print("=" * 72)
        return

    # Build output paths
    posts_dir = ROOT / "output" / "the-pla-watch" / "posts"
    pla_watch_dir = ROOT / "output" / "the-pla-watch"
    linkedin_dir = ROOT / "the-pla-watch" / "linkedin"

    posts_dir.mkdir(parents=True, exist_ok=True)
    pla_watch_dir.mkdir(parents=True, exist_ok=True)
    linkedin_dir.mkdir(parents=True, exist_ok=True)

    days_covered = len(stats["dates_covered"])
    edition_label = derive_edition_label(result["edition_type"], days_covered)
    source_trail, trail_truncated = build_source_trail(articles)

    sidecar = {
        "date": week_ending_str,
        "week_ending": week_ending_str,
        "week_start": week_start_str,
        "title": result["title"],
        "dek": result["dek"],
        "signal": result.get("signal", "") or "",
        "n_articles": stats["total_articles"],
        "n_significant": stats["n_significant"],
        "days_covered": days_covered,
        "edition_type": result["edition_type"],
        "edition_label": edition_label,
        "author_name":  AUTHOR_NAME,
        "author_title": AUTHOR_TITLE,
        "author_bio":   AUTHOR_BIO,
        "author_links": AUTHOR_LINKS,
    }

    meta = {
        **sidecar,
        "dates_covered": stats["dates_covered"],
        "sources_seen": stats["sources_seen"],
        "articles": source_trail,
        "source_trail_truncated": trail_truncated,
    }

    # Write sidecar JSON
    json_path = posts_dir / f"{week_ending_str}.json"
    json_path.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False), encoding="utf-8")

    # Render and write post HTML
    post_html = render_post(result, meta)
    html_path = posts_dir / f"{week_ending_str}.html"
    html_path.write_text(post_html, encoding="utf-8")
    print(f"Wrote post: {html_path}")

    # Write LinkedIn .txt
    linkedin_path = linkedin_dir / f"{week_ending_str}.txt"
    linkedin_path.write_text(result["linkedin_version"], encoding="utf-8")
    print(f"Wrote LinkedIn: {linkedin_path}")

    # Regenerate index and archive
    all_posts = load_existing_posts(posts_dir)
    all_posts.sort(key=lambda p: p["date"], reverse=True)

    index_html = render_index(all_posts)
    (pla_watch_dir / "index.html").write_text(index_html, encoding="utf-8")
    print(f"Wrote index: {pla_watch_dir / 'index.html'}")

    archive_html = render_archive(all_posts)
    (pla_watch_dir / "archive.html").write_text(archive_html, encoding="utf-8")
    print(f"Wrote archive: {pla_watch_dir / 'archive.html'}")

    print("Done.")


if __name__ == "__main__":
    main()
