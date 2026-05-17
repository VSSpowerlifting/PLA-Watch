"""
LLM prompt templates for PLA Watch analysis pipeline.

All four tasks share a common system prompt defined here.  Each build_*
function returns a ready-to-use messages list for anthropic.messages.create().
The *_SCHEMA dicts document the expected JSON structure for validation.

PROMPT_VERSION is written to every row in the articles table alongside the
model ID so that results can be re-analyzed against improved prompts without
losing provenance.
"""

from typing import Any

PROMPT_VERSION = "v1"

# ── Shared system prompt ──────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are an analyst supporting an open-source intelligence tool that monitors "
    "Chinese military and security developments for a U.S. national security audience. "
    '"Military and security" here includes the People\'s Liberation Army (Army, Navy, '
    "Air Force, Rocket Force, Strategic Support Force), the People's Armed Police, "
    "the China Coast Guard, and the Chinese defense industrial base (including AVIC, "
    "CSSC, CSGC, NORINCO, and CETC). Coverage of PLA cyber and information warfare "
    "units is in scope.\n\n"
    "Your audience is policy professionals and researchers who can read your output "
    "critically. Write with precision. Avoid hedging language that adds no information "
    '("it appears that," "it could be argued"). Distinguish what is reported from what '
    "is signaled. Do not editorialize beyond what the evidence supports."
)

# ── Valid category taxonomy ───────────────────────────────────────────────────
# Must match category slugs in storage/schema.sql exactly.

VALID_CATEGORIES: frozenset[str] = frozenset({
    "taiwan",
    "south_china_sea",
    "east_china_sea",
    "us_china_military",
    "exercises",
    "modernization",
    "doctrine",
    "personnel",
    "nuclear",
    "cyber_info",
    "internal_security",
    "coast_guard",
    "military_diplomacy",
    "political_work",
})


# ── Task 1: Relevance scoring ─────────────────────────────────────────────────

RELEVANCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score":     {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reasoning": {"type": "string"},
    },
    "required": ["score", "reasoning"],
}


def build_relevance_messages(title: str, body: str) -> list[dict]:
    """Score how substantively the article covers Chinese military/security topics."""
    user_content = f"""\
You will receive a Chinese-language article (title and body). Score how substantively \
it covers Chinese military or security topics, on a scale from 0.0 to 1.0.

Scoring rubric:

0.9 to 1.0: Article is centrally about PLA, PAP, CCG, or defense industry activities, \
decisions, or developments. Contains specific facts, named units, named officials, dates, \
locations, or operational details. Doctrinal essays and strategic-communications pieces \
from authoritative sources also qualify at this level when they are substantively about \
military affairs, even if they lack named specifics.

0.6 to 0.8: Military or security topic is the main subject but the article is largely \
commentary, opinion, or general context rather than new information.

0.3 to 0.5: Military or security content is present but secondary to the article's main \
subject (e.g., an economic article that mentions defense spending in passing).

0.0 to 0.2: Article is not about military or security topics in any meaningful way, \
despite triggering keyword filters.

Return only the raw JSON object with two fields: "score" (float) and "reasoning" (one \
sentence explaining the score). Do not wrap the response in markdown code fences or any \
other formatting. Do not include any other text.

Title: {title}

Body:
{body}"""
    return [{"role": "user", "content": user_content}]


# ── Task 2: Translation ───────────────────────────────────────────────────────

TRANSLATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title_en": {"type": "string"},
        "body_en":  {"type": "string"},
    },
    "required": ["title_en", "body_en"],
}


def build_translation_messages(title: str, body: str) -> list[dict]:
    """Translate a Chinese-language article to English."""
    user_content = f"""\
Translate the following Chinese article (title and body) into English.

Requirements:

Preserve the structure and paragraph breaks of the original.

Translate official titles, unit designations, and weapon system names into their \
established English equivalents (e.g., 中央军委 as "Central Military Commission," \
东部战区 as "Eastern Theater Command," 火箭军 as "Rocket Force"). Use the U.S. \
Department of Defense's preferred renderings where they exist.

For Chinese military doctrinal terms, political slogans, or concepts that lack a \
precise English equivalent, give your best translation followed by the original \
Chinese in parentheses. Examples: "intelligentized warfare (智能化战争)," \
"new-quality combat capabilities (新质战斗力)."

Do not smooth over rhetorical flourishes, ideological language, or ambiguity in the \
original. If the original is vague, the translation should also be vague. If the original \
uses heightened language ("决不", "坚决", "绝不容忍"), preserve that register.

Do not add explanatory content that is not in the original. Translation only.

Return only the raw JSON object with two fields: "title_en" (string) and "body_en" \
(string). Do not wrap the response in markdown code fences or any other formatting.

Title: {title}

Body:
{body}"""
    return [{"role": "user", "content": user_content}]


# ── Task 3: Analytic summary ──────────────────────────────────────────────────

SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
    },
    "required": ["summary"],
}


def build_summary_messages(title_en: str, body_en: str) -> list[dict]:
    """Generate a two-to-three sentence analytic summary for a policy audience."""
    user_content = f"""\
Write a two to three sentence analytic summary for a U.S. national security analyst \
tracking Chinese military media. The prose should be direct and specific, not \
institutional in register.

The first sentence reports what the article says: who did what, when, where, and at \
what scale. Be concrete and specific. Use named units, named officials, and named \
locations where the article provides them.

The second (and optional third) sentence answers: what concrete institutional or \
military problem does this article make visible? What specific pattern does it fit, \
break, or document? Name the problem before naming the pattern. Prefer specific verbs \
over generic ones: records, documents, demonstrates, shows, confirms, reveals, fits, \
breaks, extends, complicates, raises the question of. Do not default to "signals."

If the article is genuinely routine, name the specific category of routine and its \
narrow use — e.g., "This is a quarterly readiness assessment; it documents the \
detachment's training cycle but does not indicate changed posture." or "This is \
standard political work content; its value is as a record of how the institution \
frames loyalty to junior officers, not as evidence of a new policy line." Do not use \
the phrase "contains no new information." Say specifically what kind of baseline the \
article provides.

Voice and style:

Declarative. Make claims the evidence supports rather than gesturing at possibilities.
Precise. Prefer specific nouns and verbs over general ones.
Avoid hedging language ("it appears," "it could be argued," "this may suggest," \
"potentially") unless the underlying uncertainty is genuine and material.
Avoid meta-language. Do not begin with "This article reports" or "The piece discusses." \
Start with the substance.
Do not use "signals" or "reflects continued" as default second-sentence openers — they \
are overused across this corpus and flatten distinct articles into the same register. \
Find the more specific verb.

Length: two to three sentences. Do not exceed three.
Return only the raw JSON object with one field: "summary" (string). Do not wrap the \
response in markdown code fences or any other formatting.

Title: {title_en}

Body:
{body_en}"""
    return [{"role": "user", "content": user_content}]


# ── Task 4: Category tagging + significance flag ──────────────────────────────

CATEGORY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "categories": {
            "type": "array",
            "items": {"type": "string", "enum": sorted(VALID_CATEGORIES)},
        },
        "significance":        {"type": "boolean"},
        "significance_reason": {"type": ["string", "null"]},
    },
    "required": ["categories", "significance", "significance_reason"],
}


def build_category_messages(title_en: str, body_en: str) -> list[dict]:
    """Assign fixed-taxonomy categories and a conservative significance flag."""
    user_content = f"""\
Assign categories and a significance flag to the article.

Categories: Choose all that apply from this fixed list. Do not invent new categories. \
An article can have one or more categories.

- coast_guard (CCG operations, gray-zone maritime)
- cyber_info (cyber operations, information warfare, SSF activities)
- doctrine (doctrinal essays, strategic concepts, theoretical writing)
- east_china_sea (Senkaku/Diaoyu, Japan-related maritime)
- exercises (training, drills, joint exercises)
- internal_security (PAP, Xinjiang, Tibet, Hong Kong contingencies)
- military_diplomacy (military-to-military diplomacy, exercises with foreign militaries, arms exports)
- modernization (new platforms, capabilities, defense industry developments)
- nuclear (nuclear forces, Rocket Force, strategic weapons)
- personnel (promotions, removals, anti-corruption actions)
- political_work (party-army relations, ideological campaigns within PLA)
- south_china_sea (SCS, Spratly, Paracel, Scarborough)
- taiwan (Taiwan Strait, cross-strait, Taiwan-related operations)
- us_china_military (direct U.S.-China military interactions, FONOPs, intercepts)

Significance flag: Set to true only if there is a specific, articulable reason the \
article is unusual relative to baseline PLA reporting. Examples of what qualifies:

First public reporting of a new capability, platform, unit, or exercise type.
A named senior official appearing or disappearing in an analytically meaningful way.
A break from established rhetorical patterns or doctrinal positions.
An operational event of scale or location that exceeds the routine.
An admission, signal, or detail that would be of interest to a working analyst \
tracking this topic.

Examples of what does NOT qualify:

Routine training exercises with no unusual features.
Standard ideological or political work content.
Generic statements of resolve or capability without new information.
Personnel announcements that follow predictable patterns.

Across a typical week of PLA Daily and MOD coverage, roughly 1 in 20 articles meets \
this bar. If you flag more than that, your threshold is too low.

If you set the flag to true, you must provide a one-sentence reason in \
"significance_reason." If false, "significance_reason" should be null.

Return only the raw JSON object with three fields: "categories" (array of strings from \
the fixed list), "significance" (boolean), "significance_reason" (string or null). Do \
not wrap the response in markdown code fences or any other formatting.

Title: {title_en}

Body:
{body_en}"""
    return [{"role": "user", "content": user_content}]
