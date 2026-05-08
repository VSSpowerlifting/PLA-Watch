"""
Central configuration for PLA Watch.
Edit values here; load secrets from .env (never commit .env).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT_DIR   = Path(__file__).parent
CACHE_DIR  = ROOT_DIR / "cache"
OUTPUT_DIR = ROOT_DIR / "output"
DB_PATH    = ROOT_DIR / "pla_watch.db"

# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")

# ---------------------------------------------------------------------------
# HTTP behavior
# ---------------------------------------------------------------------------
REQUEST_DELAY_SECONDS:   float = 2.5   # Minimum gap between outbound requests
REQUEST_TIMEOUT_SECONDS: int   = 30
MAX_RETRIES:             int   = 3

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
RELEVANCE_THRESHOLD: float = 0.60      # LLM confidence score; articles below this are filtered
# Model string as specified by project design doc.  If the API returns a model-not-found
# error, verify the current ID at https://docs.anthropic.com/en/docs/about-claude/models
ANALYSIS_MODEL: str = "claude-sonnet-4-6"
PROMPT_VERSION: str = "v1"

# ---------------------------------------------------------------------------
# Keyword pre-filter
# An article must match at least one keyword to proceed to the LLM pass.
# Scope: PLA + PLAN/PLAAF/PLARF/PLASSF + PAP + Coast Guard + defense industry
#        + Taiwan/SCS/ECS gray-zone + cyber/info warfare
# ---------------------------------------------------------------------------
RELEVANCE_KEYWORDS_ZH: list[str] = [
    # Core institutions
    "解放军", "人民解放军", "军委", "中央军委", "国防部", "战区",
    # Services and branches
    "海军", "空军", "火箭军", "陆军", "战略支援部队", "联合参谋部",
    "海警", "武警", "人民武装警察",
    # Platforms and systems
    "导弹", "航母", "舰", "潜艇", "歼", "轰", "运", "直", "无人机",
    "高超音速", "核", "弹道导弹", "巡航导弹",
    # Operations and readiness
    "演习", "军演", "实弹", "联合作战", "战备", "巡逻", "侦察",
    # Geographic flashpoints
    "台湾", "台海", "南海", "东海", "钓鱼岛", "黄岩岛", "渤海",
    # Modernization / industry
    "国防工业", "装备", "采购", "研制", "航空工业", "中船集团",
    "兵器工业", "航天科工", "航天科技",
    # Doctrine / information domain
    "信息化", "智能化", "网络战", "信息战", "认知战", "心理战",
    "电子战", "太空", "网络空间",
    # Internal security (PAP-relevant)
    "新疆", "西藏", "香港", "反恐", "维稳",
    # Leadership / political work
    "军事委员会", "政治工作", "习近平主席", "国防",
]

RELEVANCE_KEYWORDS_EN: list[str] = [
    # Institutions
    "PLA", "People's Liberation Army", "CMC", "Central Military Commission",
    "Ministry of National Defense", "MND",
    "People's Armed Police", "PAP", "China Coast Guard",
    # Services
    "PLAN", "PLAAF", "PLARF", "PLASSF", "PLA Navy", "PLA Air Force",
    "PLA Rocket Force",
    # Operations
    "military exercise", "live-fire", "joint exercise", "patrol", "drill",
    "deployment", "readiness",
    # Platforms
    "aircraft carrier", "destroyer", "submarine", "fighter jet", "bomber",
    "missile", "hypersonic", "nuclear", "ballistic", "cruise missile",
    "drone", "UAV",
    # Flashpoints
    "Taiwan", "South China Sea", "East China Sea", "Senkaku", "Diaoyu",
    "Scarborough", "Spratlys", "Paracels",
    # Modernization
    "defense industry", "AVIC", "CSSC", "procurement", "weapons system",
    # Doctrine / information
    "cyber", "information warfare", "cognitive warfare", "electronic warfare",
    "space", "counterspace",
    # Misc
    "defense", "military",
]
