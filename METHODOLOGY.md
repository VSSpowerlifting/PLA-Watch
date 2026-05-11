# PLA Watch — Methodology

This document describes the sourcing rationale, analytical framework, and known
limitations of PLA Watch.  It is intended to be read alongside the tool output
and before drawing any conclusions from it.  Anyone using this tool for research
or analysis should read this document first.

---

## 1. Source Selection

China Mil Watch currently centers production coverage on PLA Daily and is
configured for expansion across additional publicly accessible official and
state-linked sources, including MND, China Military Online, Global Times
Military, and Xinhua Military. Each source was selected for a distinct
analytical purpose.

| Source | Language | Purpose |
|--------|----------|---------|
| **PLA Daily (解放军报)** `81.cn` | Chinese | Authoritative CMC-attributed statements; official PLA narrative |
| **Ministry of National Defense** `mod.gov.cn` | Chinese | MND press releases, spokesperson statements, official policy |
| **Xinhua Military** `xinhuanet.com` | Chinese | Wider distribution of PLA/MND items; sometimes publishes faster than 81.cn |
| **Global Times — Defense** `globaltimes.cn` | English | Nationalistic commentary; signals PLA messaging to foreign audiences |
| **China Military Online** `english.chinamil.com.cn` | English | Official English-language mirror; used for cross-reference and translation anchor |

### What these sources are

All five outlets are organs of the Chinese state.  PLA Daily and China Military
Online are published directly under the CMC's Political Work Department.
Xinhua is a state news agency with mandatory distribution across government
entities.  Global Times is published by People's Daily Group under CCP
supervision and consistently reflects an officially tolerated nationalist line.

This means: **the absence of coverage is as analytically significant as the
presence of it.**  What the PLA chooses to publicize—exercises, weapons
demonstrations, personnel changes, doctrinal statements—is a deliberate act of
signaling.  What it omits is equally deliberate.

### Coverage Status

China Mil Watch currently monitors PLA Daily and is being expanded across
additional official and state-linked sources including MND, China Military
Online, Global Times Military, and Xinhua Military. Some sources may return
zero articles on a given day; Xinhua Military remains in development because
its listings are JavaScript/API-rendered.

### What these sources are not

None of these sources provides unfiltered or objective reporting on PLA
activities.  They do not report operational security failures, internal
factional disputes, equipment performance shortfalls, or events the CMC has
determined should not be publicized.  This tool cannot surface what Chinese
state media does not publish.

---

## 2. Scope of Coverage

The relevance filter is intentionally broad.  Coverage includes:

- **Core uniformed services**: PLA Army (PLAA), Navy (PLAN), Air Force (PLAAF),
  Rocket Force (PLARF), Strategic Support Force (PLASSF/successor units)
- **People's Armed Police (PAP)** and China Coast Guard (CCG), which operate
  under CMC authority and are analytically relevant to gray-zone and internal
  security questions
- **Defense industry**: AVIC, CSSC, CSGC, CASC, and other state defense
  conglomerates, whose procurement and test announcements signal modernization
  timelines
- **Cyber, information warfare, and space operations**, given their centrality
  to PLA doctrine and ongoing U.S. policy concern
- **Geographic flashpoints**: Taiwan, South China Sea, East China Sea, and
  adjacent maritime zones

Breadth is managed through category tagging, not exclusion at the filter stage.
Users who want a PLA-only view can filter to exclude PAP and defense industry
items.

---

## 3. Relevance Filtering

### Stage 1 — Keyword pre-filter

A Chinese and English keyword list (defined in `config.py`) screens scraped
content.  An article must match at least one keyword to proceed to the LLM
stage.  This stage is designed for high recall; it accepts false positives
(articles mentioning "军事" in passing) rather than risking false negatives.

### Stage 2 — LLM relevance scoring

Keyword-passing articles are submitted to a Claude model with a structured
prompt requesting a relevance confidence score (0.0–1.0) and brief reasoning.
The threshold for inclusion is configurable (default: 0.60).

The LLM prompt asks the model to assess whether an article contains substantive
information about PLA/PAP/CCG activities, capabilities, posture, or policy—not
merely whether it mentions military terminology.  An article about a government
official visiting a military base for a civilian ceremony would score low; an
article announcing a new weapons system entering service would score high.

Both the score and the reasoning are stored in the database.  Lowering the
threshold recovers borderline items; raising it produces a tighter, more
signal-dense feed.

---

## 4. Translation

Chinese-language articles are translated to English using the Claude API.
Several limitations apply:

1. **Military technical terminology** does not always have established English
   equivalents.  The translation prompt instructs the model to preserve Chinese
   terms in parentheses where no standard English translation exists (e.g.,
   "联合利剑" is translated as "Joint Sword" with the Chinese retained).

2. **Doctrinal concepts** (e.g., 信息化, 智能化, 体系作战) carry meaning
   that is compressed in translation.  The analytic summary is intended to
   surface this context, but users working on doctrinal questions should consult
   the original Chinese text, which is preserved in the database.

3. **LLM translation is not authoritative**.  For any article of operational
   significance, verify the translation against the original text or a qualified
   human translator.

The original Chinese text is stored alongside every translation so that
improved prompts or manual corrections can be applied without re-scraping.

---

## 5. Analytic Summaries and Significance Flags

### Summary

The two-to-three sentence summary aims to answer: *what is being reported, and
why does it matter in the context of PLA modernization and regional security?*
It is not a neutral restatement of the article.  It is an analytical condensate.

Summaries are generated by a Claude model using a prompt that explicitly
discourages descriptive language ("this article reports that...") in favor of
analytic framing ("the announcement confirms/signals/continues a pattern of...").

### Significance flag

The significance flag is conservative.  It is triggered only when the LLM
identifies a specific, articulable reason why an item is unusual relative to
baseline PLA reporting patterns.  Routine exercise announcements, standard
political work reports, and formulaic spokesperson statements will not be
flagged even if they contain military keywords.

Items that typically trigger the flag include: first-of-kind capability
announcements, operational deployments with no announced exercise context,
senior personnel changes not telegraphed in advance, and statements that
represent a departure from established official lines on Taiwan or the South
China Sea.

The significance reasoning field records the model's articulation of why the
flag was set.  This field should be read critically—LLM significance judgment
is an assistive tool, not a substitute for analyst review.

---

## 6. Known Limitations

| Limitation | Implication |
|-----------|-------------|
| OSINT only | Activities not publicized by Chinese state media are not captured |
| State media bias | All sources have editorial lines aligned with CCP and CMC interests |
| No classified sources | This tool cannot supplement or verify classified assessments |
| Translation artifacts | Technical and doctrinal terms may be imprecisely rendered |
| Scraper fragility | CSS selectors break when source sites are redesigned; monitoring required |
| LLM errors | Relevance scores, summaries, and significance flags can be wrong |
| Daily cadence | Intra-day developments are not captured until the next run |
| Archive depth | This tool begins capturing from its first deployment; no historical backfill |

---

## 7. What This Tool Is Appropriate For

- Tracking the public PLA narrative over time
- Building a structured archive of official Chinese military statements
- Identifying surface-level patterns in PLA exercise frequency, geographic
  focus, or capability emphasis
- Learning to read Chinese military media with analytic scaffolding
- Portfolio demonstration of OSINT methodology and data pipeline design

## 8. What This Tool Is Not Appropriate For

- Making assessments about PLA capabilities or intentions beyond what is
  explicitly stated in official Chinese sources
- Generating intelligence products without human analyst review
- Replacing expert judgment on questions of military significance
- Any classified or official government use

---

*PLA Watch is an independent academic project.  It is not affiliated with any
government agency, think tank, or research institution.  All conclusions drawn
from this tool are the responsibility of the analyst using it.*
