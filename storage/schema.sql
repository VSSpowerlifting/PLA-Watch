-- PLA Watch — canonical database schema
-- Run once at setup; safe to re-run (all CREATE statements use IF NOT EXISTS).
--
-- Design notes:
--   • articles.text_original preserved so translation can be rerun against
--     improved prompts without re-scraping.
--   • relevance_score (0.0–1.0) stored so threshold can be tuned offline.
--   • article_categories is a join table for multi-label categorization.
--   • scrape_runs provides an audit log for debugging pipeline failures.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── Sources ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sources (
    id           INTEGER PRIMARY KEY,
    slug         TEXT    NOT NULL UNIQUE,
    display_name TEXT    NOT NULL,
    base_url     TEXT    NOT NULL,
    language     TEXT    NOT NULL CHECK (language IN ('zh', 'en')),
    is_active    INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Categories ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS categories (
    id           INTEGER PRIMARY KEY,
    slug         TEXT    NOT NULL UNIQUE,
    display_name TEXT    NOT NULL,
    description  TEXT
);

-- ── Scrape run log ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS scrape_runs (
    id                INTEGER PRIMARY KEY,
    started_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    completed_at      TEXT,
    articles_scraped  INTEGER NOT NULL DEFAULT 0,
    articles_new      INTEGER NOT NULL DEFAULT 0,
    articles_analyzed INTEGER NOT NULL DEFAULT 0,
    errors            TEXT,   -- JSON array of error strings
    status            TEXT    NOT NULL DEFAULT 'running'
                              CHECK (status IN ('running', 'completed', 'failed'))
);

-- ── Articles ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS articles (
    id                    INTEGER PRIMARY KEY,
    url                   TEXT    NOT NULL UNIQUE,
    content_hash          TEXT    NOT NULL,          -- SHA-256 of (title + text); dedup signal
    source_id             INTEGER NOT NULL REFERENCES sources(id),
    scrape_run_id         INTEGER REFERENCES scrape_runs(id),

    -- Raw content (language of origin)
    title_original        TEXT,
    text_original         TEXT,
    published_date        TEXT,                      -- ISO-8601 date (YYYY-MM-DD)
    scraped_at            TEXT    NOT NULL DEFAULT (datetime('now')),

    -- Relevance gate
    relevance_score       REAL,                      -- LLM confidence 0.0–1.0; NULL = not assessed
    relevance_reasoning   TEXT,                      -- LLM's brief rationale
    passed_relevance      INTEGER,                   -- 1 = kept, 0 = filtered, NULL = pending

    -- Analysis output (populated only for passed_relevance = 1)
    title_english         TEXT,
    text_english          TEXT,
    summary_english       TEXT,                      -- 2–3 sentence analytic summary
    analyzed_at           TEXT,

    -- Significance flag
    is_significant        INTEGER NOT NULL DEFAULT 0,
    significance_reasoning TEXT,

    -- Provenance — enables reanalysis with newer prompts/models
    model_id              TEXT,                      -- e.g. "claude-opus-4-7"
    prompt_version        TEXT                       -- e.g. "v1.0"
);

-- ── Article ↔ Category (multi-label) ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS article_categories (
    article_id  INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    PRIMARY KEY (article_id, category_id)
);

-- ── Indexes ──────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_articles_published   ON articles(published_date DESC);
CREATE INDEX IF NOT EXISTS idx_articles_source      ON articles(source_id);
CREATE INDEX IF NOT EXISTS idx_articles_relevance   ON articles(passed_relevance, relevance_score DESC);
CREATE INDEX IF NOT EXISTS idx_articles_significant ON articles(is_significant) WHERE is_significant = 1;
CREATE INDEX IF NOT EXISTS idx_articles_hash        ON articles(content_hash);
CREATE INDEX IF NOT EXISTS idx_article_categories   ON article_categories(category_id);

-- ── Seed data ────────────────────────────────────────────────────────────────

INSERT OR IGNORE INTO sources (slug, display_name, base_url, language) VALUES
    ('pla_daily',        'PLA Daily (解放军报)',          'https://www.81.cn',                 'zh'),
    ('mod_china',        'MOD China (国防部)',             'http://www.mod.gov.cn',             'zh'),
    ('xinhua_mil',       'Xinhua Military (新华军事)',     'https://www.xinhuanet.com',         'zh'),
    ('global_times_mil', 'Global Times — Defense',       'https://www.globaltimes.cn',        'en'),
    ('china_mil_online', 'China Military Online (EN)',   'http://english.chinamil.com.cn',    'en');

-- Category slugs must match VALID_CATEGORIES in analysis/prompts.py exactly.
-- The LLM uses this fixed taxonomy; any mismatch silently drops categories in db.update_analysis().
INSERT OR IGNORE INTO categories (slug, display_name, description) VALUES
    ('taiwan',             'Taiwan',               'Taiwan Strait, cross-strait military posture, Taiwan-related operations'),
    ('south_china_sea',    'South China Sea',      'SCS, Spratly, Paracel, Scarborough Shoal activity'),
    ('east_china_sea',     'East China Sea',       'Senkaku/Diaoyu, Japan-related maritime'),
    ('us_china_military',  'U.S.–China Military',  'Direct U.S.-China military interactions, FONOPs, intercepts'),
    ('exercises',          'Exercises',            'Training, drills, joint exercises'),
    ('modernization',      'Modernization',        'New platforms, capabilities, defense industry developments'),
    ('doctrine',           'Doctrine',             'Doctrinal essays, strategic concepts, theoretical writing'),
    ('personnel',          'Personnel',            'Promotions, removals, anti-corruption actions'),
    ('nuclear',            'Nuclear',              'Nuclear forces, Rocket Force, strategic weapons'),
    ('cyber_info',         'Cyber & Information',  'Cyber operations, information warfare, SSF activities'),
    ('internal_security',  'Internal Security',    'PAP, Xinjiang, Tibet, Hong Kong contingencies'),
    ('coast_guard',        'Coast Guard',          'CCG operations, gray-zone maritime'),
    ('military_diplomacy', 'Military Diplomacy',   'Mil-to-mil diplomacy, exercises with foreign militaries, arms exports'),
    ('political_work',     'Political Work',       'Party-army relations, ideological campaigns within PLA');
