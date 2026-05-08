# PLA Watch — v2 Roadmap

Items scoped out of v1 during initial development.  Each entry includes
the failure mode or limitation that motivated it and a rough implementation
direction.  Ordered roughly by analytical impact.

---

## P1 — High impact, clear implementation path

### Structured output for LLM analysis tasks
**Motivation:** The current `_parse_json()` method in `analysis/analyzer.py`
strips markdown code fences via regex.  Long responses — particularly
multi-thousand-character translation outputs for doctrinal and historical
essays — intermittently produce formatting drift that bypasses the regex.
Two articles failed on a 23-article batch (8.7% failure rate on long content).

**Fix:** Migrate all four analysis tasks to the Anthropic API's tool-use /
structured output mode.  Pass each task's JSON schema as a `tools` definition
and set `tool_choice` to force a structured response.  This eliminates
free-text JSON parsing entirely.

**Affected file:** `analysis/analyzer.py` — `_call()` and all four task methods.

---

### Cadence-aware summaries for routine patrol/exercise reporting
**Motivation:** The current summary prompt produces analytically correct but
context-free descriptions of routine events.  For CCG Diaoyu patrols, PLAN
exercise announcements, and similar recurring operations, "follows a standard
pattern" is accurate but not maximally useful.  A summary that says "the 4th
CCG patrol of the Diaoyu Islands in 7 days, compared to a baseline of ~2/week
in Q1 2026" is more actionable.

**Fix:** After accumulating 30+ days of archive depth, add a context-injection
step before the summary prompt: query the DB for prior articles matching the
same category + geographic area within a configurable lookback window, extract
cadence data, and pass it as a `[CONTEXT]` block.

**Dependency:** Requires archive depth to be meaningful.  Revisit mid-summer.

**Affected files:** `analysis/prompts.py` (summary prompt), `analysis/analyzer.py`
(pre-prompt context fetch), `storage/db.py` (cadence query).

---

## P2 — Medium impact, some complexity

### Tighten relevance filter for classical military history content
**Motivation:** The keyword pre-filter passes articles on ancient/classical
Chinese military history (e.g., Battle of Changping analysis, 孙子兵法 essays)
because they contain military terminology.  These score 0.6–0.7 on LLM
relevance — technically above threshold — but carry no intelligence value
about current PLA posture, capabilities, or activities.

**Fix (option A):** Add a clause to the relevance scoring prompt: "Articles
whose primary subject is pre-20th-century military history, classical military
philosophy, or historical fiction — even if written with PLA political-work
framing — should score 0.1–0.3 unless they contain specific claims about
current PLA doctrine, capabilities, or unit activities."

**Fix (option B):** Add a keyword blocklist for classical-history signals
(长平之战, 赤壁, 孙子, 三十六计, etc.) that downgrades, but does not eliminate,
articles from the LLM relevance pass.

Option A is cleaner and doesn't require maintaining a blocklist.

**Affected file:** `analysis/prompts.py` — `build_relevance_messages()`.

---

### Resume robustness for translation failures
**Motivation:** Articles whose translation fails (Stage 2) have
`passed_relevance=1` and `analyzed_at=NULL` and are correctly picked up
by `get_articles_pending_analysis()` on re-run.  However, re-running
relevance scoring (Stage 1) on articles that already passed wastes one
API call per article.

**Fix:** Add a `skip_relevance` flag to `Analyzer.analyze()`.  The pipeline
passes `skip_relevance=True` for articles sourced from
`get_articles_pending_analysis()` since their relevance is already confirmed.

**Affected files:** `analysis/analyzer.py`, `pipeline.py`.

---

## P3 — Low priority / post-MVP

### Xinhua Military scraper (JS-rendered)
`xinhua_mil` is implemented as a stub.  `xinhuanet.com/mil/` renders
article listings entirely via JavaScript API calls and returns only stale
2020-era HTML to requests-based clients.  Three options are documented in
`scraper/sources/xinhua_mil.py`: headless browser (Playwright/Selenium),
reverse-engineering the `xhpfmapi.zhongguowangshi.com` API, or substituting
a different Chinese-language military source.

### Static site generator
Jinja2 templates in `site/` producing daily brief, searchable archive,
category filters.  Depends on having enough archive depth to make the archive
view non-trivial.

### GitHub Actions deployment
`.github/workflows/daily_update.yml` — runs pipeline at 06:00 UTC, commits
output to `gh-pages`.  Straightforward once the site generator exists.

### Cross-source deduplication signal quality
Current deduplication uses URL and SHA-256 content hash.  Xinhua and China
Military Online frequently republish PLA Daily content with minor edits,
which will pass the content-hash check.  A fuzzy-match approach (e.g.,
MinHash or simple title similarity) would catch near-duplicates across sources.
