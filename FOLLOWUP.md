## Followup: persistent title-hash dedup
Current dedup_articles() only checks within the current batch. URL and content_hash DB checks won't catch syndicated reposts on later dates because both diverge across reposts. Fix:
- Add title_hash column to articles table with an index.
- Populate at insert time in processing/metadata.py alongside content_hash.
- Add db.title_hash_exists() and call it inside dedup_articles().
- Migration to backfill title_hash for existing rows.
Do not bundle with the current dedup patch. Land this as a separate change after the current fix has been live for a few days.

## Followup: site generator hygiene
- Orphan article HTML cleanup is now in site/generator.py (added with the
  title-dedup patch). If we add other generated artifact types in the
  future (per-category pages, per-source pages, RSS items), each new
  generator function needs its own orphan-cleanup pass or a shared
  utility for "given a set of expected output files, prune everything
  else in this directory."
