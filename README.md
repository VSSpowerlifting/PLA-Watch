# PLA Watch

An open-source intelligence tool that monitors Chinese military and security
developments from Chinese-language state media sources, translates and
summarizes daily reporting, and publishes a structured brief to a static
website updated on a 24-hour cycle.

This is an academic portfolio project.  It does not use or claim access to any
classified information.  All source material is publicly available.

---

## What It Does

PLA Watch scrapes five Chinese state media outlets daily, filters content for
military and security relevance, translates Chinese-language articles to
English, generates analytic summaries, and flags items of unusual significance.
Results are stored in a local SQLite database and published as a static site
suitable for hosting on GitHub Pages.

The tool is designed to reduce the friction of monitoring official Chinese
military media for analysts, researchers, and students who cannot read
Chinese at speed, while preserving the original source text for those who can.

---

## Sources

| Source | Language | Coverage |
|--------|----------|---------|
| PLA Daily (`81.cn`) | Chinese | CMC-attributed statements; official PLA narrative |
| Ministry of National Defense (`mod.gov.cn`) | Chinese | MND press releases; spokesperson statements |
| Xinhua Military (`xinhuanet.com`) | Chinese | Amplified PLA/MND items |
| Global Times Defense (`globaltimes.cn`) | English | Official-line commentary for foreign audiences |
| China Military Online (`english.chinamil.com.cn`) | English | English mirror of PLA Daily ecosystem |

All sources are organs of the Chinese state.  See [METHODOLOGY.md](METHODOLOGY.md)
for a full discussion of source biases and what these outlets do and do not
report.

---

## Methodology

The relevance filter, translation approach, analytic summary framework, and
significance flag criteria are documented in detail in [METHODOLOGY.md](METHODOLOGY.md).

Short version: keyword pre-filter → LLM relevance scoring → LLM
translation and summary → category tagging → significance flag.
Everything is stored; thresholds are tunable.

---

## Limitations

- **OSINT only.**  This tool does not surface anything the PLA has not chosen
  to publicize.
- **Machine translation.**  Chinese military and doctrinal terminology does not
  always translate cleanly.  Original text is preserved; treat translations
  as assistive, not authoritative.
- **LLM errors.**  Relevance scores, summaries, and significance flags can be
  wrong.  Review the source before acting on a flag.
- **Scraper fragility.**  CSS selectors break when sites redesign.  Check the
  run log if articles stop appearing.
- No historical data prior to first deployment.

---

## Project Structure

```
pla-watch/
├── scraper/            # Source-specific scrapers (one class per source)
│   └── sources/
├── processing/         # Dedup, keyword filter, metadata normalization
├── analysis/           # LLM translation, summary, categorization (prompts.py)
├── storage/            # SQLite schema and data access layer
├── site/               # Jinja2 static site generator
├── .github/workflows/  # GitHub Actions daily scheduler
├── cache/              # Raw HTML cache (gitignored)
├── output/             # Generated static site (published to gh-pages)
├── pipeline.py         # Main pipeline runner
├── config.py           # All tunables and keyword lists
├── METHODOLOGY.md      # Sourcing rationale, caveats, analytical framework
└── README.md
```

---

## Setup

### Prerequisites

- Python 3.11+
- An [Anthropic API key](https://console.anthropic.com/)
- A GitHub account

### Local installation

```bash
git clone https://github.com/[username]/pla-watch.git
cd pla-watch
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY=your_key_here
```

### Initialize the database and run a test scrape

```bash
# Scrape PLA Daily only, no DB writes (for testing)
python pipeline.py --source pla_daily --dry-run

# Full run against all sources
python pipeline.py
```

### Generate the site

```bash
# Not yet implemented — see site/ directory
python site/generator.py
```

---

## Deployment (GitHub Pages + GitHub Actions)

### 1. Create the repository

Create a new **public** repository named `pla-watch` on GitHub.
Push your local code to the `main` branch.

### 2. Add the API key as a secret

In your repository: **Settings → Secrets and variables → Actions → New repository secret**

- Name: `ANTHROPIC_API_KEY`
- Value: your Anthropic API key

### 3. Enable GitHub Pages

In your repository: **Settings → Pages**
- Source: `Deploy from a branch`
- Branch: `gh-pages` / `/ (root)`

GitHub will create the `gh-pages` branch automatically on first workflow run.

### 4. The workflow

The file at `.github/workflows/daily_update.yml` runs the pipeline at 06:00
UTC daily, commits the updated static site to `gh-pages`, and triggers a Pages
deployment.  Your site will be live at:

```
https://[username].github.io/pla-watch/
```

---

## Contributing

This is a portfolio project, but issues and pull requests are welcome.
If you find a scraper is broken due to a site redesign, open an issue with
the new HTML structure.  If you improve a prompt, open a PR with before/after
examples showing the change in output quality.

---

## License

MIT.  See LICENSE.

---

*This project is independent academic work.  It is not affiliated with any
government agency, research institution, or think tank.  It does not use
classified information.*
