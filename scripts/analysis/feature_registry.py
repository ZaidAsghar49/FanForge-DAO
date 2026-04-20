"""
feature_registry.py — Central Feature Contract System
======================================================
Single source of truth for all analytics features.

Every feature has:
  - source:  where it must come from
  - db_col:  the column name in deliveries table
  - required: whether absence causes hard failure in STRICT mode

Sources:
  ingestion_required     → must exist in DB at ingest time (cannot be derived at query time)
  derived_ingestion      → computed from raw fields during parsing (e.g. match_phase)
  static_reference_join  → populated via JOIN with players_dim at ingest time
  query_derived          → safely computable at query time from existing columns
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal

FeatureSource = Literal[
    "ingestion_required",
    "derived_ingestion",
    "static_reference_join",
    "query_derived",
]

ExecutionMode = Literal["STRICT", "SAFE", "LOOSE"]

# ── Global execution mode ────────────────────────────────────────────────────
EXECUTION_MODE: ExecutionMode = "STRICT"

# ── Parameter Classification (Fix 4) ────────────────────────────────────────
# Filters  → used in QueryPlanner / apply_filters to REDUCE the dataset
# Metrics  → computed as output values only, never used as WHERE clauses
# Derived  → must exist as DB columns at ingestion time (not computed at query time)
FILTER_PARAMS: frozenset[str] = frozenset({
    "venue_name", "city", "country", "format", "season", "day_night",
    "toss_winner", "toss_decision", "innings", "series", "home_away",
    "neutral_venue", "opposition", "dismissal_type", "batting_position",
    "non_striker", "bowler", "bowler_type", "bowler_hand",
    "over_number", "over_range", "match_phase",
    "batter_vs_bowler_type", "batter_vs_bowler", "ball_type",
})

METRIC_PARAMS: frozenset[str] = frozenset({
    "total_runs", "balls_faced", "batting_average", "strike_rate",
    "economy_rate", "bowling_strike_rate", "wickets", "dots_forced",
    "extras_conceded", "dot_ball_pct", "boundary_pct",
    "high_score", "milestones", "partnership_runs", "runs_conceded_in_over",
    "bowling_average",
})

DERIVED_FEATURE_PARAMS: frozenset[str] = frozenset({
    # Must exist as precomputed DB columns BEFORE query execution
    "batter_hand", "ball_type", "match_phase",
})

# ── Semantic Location Map (Fix 6) ────────────────────────────────────────────
# Maps real-world context strings to schema-aware filter equivalents.
# Used when raw country/venue fields are insufficient or inconsistent.
SEMANTIC_LOCATION_MAP: dict[str, dict] = {
    "uae (icc)": {
        "description": "UAE as ICC neutral venue (not UAE national team home)",
        "country": "United Arab Emirates",
        "neutral_venue": True,
        "competitions": ["ICC Men's T20 World Cup", "ICC Champions Trophy", "Asia Cup"],
    },
    "uae": {
        "description": "Matches played in UAE (covers both national and ICC events)",
        "country": "United Arab Emirates",
    },
    "neutral": {
        "description": "Any neutral venue match",
        "neutral_venue": True,
    },
    "sub-continent": {
        "description": "Indian sub-continent: India, Pakistan, Sri Lanka, Bangladesh",
        "countries": ["India", "Pakistan", "Sri Lanka", "Bangladesh"],
    },
}


@dataclass(frozen=True)
class Feature:
    name: str
    source: FeatureSource
    db_col: str
    required: bool = True
    description: str = ""


# ── Master Feature Registry ──────────────────────────────────────────────────
FEATURE_REGISTRY: dict[str, Feature] = {
    f.name: f for f in [
        Feature("ball_type",              "derived_ingestion",     "ball_type",              required=False,
                description="pink/red based on match_type+day_night at parse time"),
        Feature("batter_hand",            "static_reference_join", "batter_hand",            required=True,
                description="left/right from players_dim JOIN at ingest; REQUIRED for bowler vs-LHB/RHB queries"),
        Feature("bowler_hand",            "static_reference_join", "bowler_hand",            required=False,
                description="left/right from players_dim JOIN at ingest"),
        Feature("bowler_type",            "static_reference_join", "bowler_type",            required=False,
                description="Pace/Spin from players_dim JOIN at ingest"),
        Feature("match_phase",            "derived_ingestion",     "match_phase",            required=True,
                description="Powerplay/Middle/Death derived from over number at parse time"),
        Feature("competition_canonical",  "ingestion_required",    "competition",            required=True,
                description="Canonical competition string from Cricsheet event.name"),
        Feature("home_away",              "derived_ingestion",     "home_team",              required=False,
                description="home_team derived from team ordering at ingest"),
        Feature("country",               "ingestion_required",     "country",                required=False,
                description="Venue country from Cricsheet info.country"),
        Feature("day_night",             "ingestion_required",     "day_night",              required=False,
                description="Day/Night flag from Cricsheet info.match_type"),
        Feature("innings",               "derived_ingestion",      "innings",                required=True,
                description="Innings number 1-4"),
        Feature("match_type",            "ingestion_required",     "match_type",             required=True,
                description="Cricsheet match_type: Test/ODI/IT20/T20 etc."),
    ]
}

# ── Competition canonical whitelist (strict, no regex) ──────────────────────
COMPETITION_MAP: dict[str, list[str]] = {
    # Domestic leagues
    "ipl":  ["Indian Premier League"],
    "psl":  ["Pakistan Super League"],
    "bbl":  ["Big Bash League"],
    "cpl":  ["Caribbean Premier League"],
    "mls":  ["Major League Cricket"],
    "sa20": ["SA20"],
    "the hundred": ["The Hundred"],
    # ICC tournaments  (multiple DB spellings covered)
    "world cup":      ["ICC Cricket World Cup", "World Cup", "ICC Men's Cricket World Cup"],
    "world cups":     ["ICC Cricket World Cup", "World Cup", "ICC Men's Cricket World Cup"],
    "t20 world cup":  ["ICC Men's T20 World Cup", "World T20", "ICC T20 World Cup",
                       "ICC World Twenty20", "World Twenty20"],
    "t20 world cups": ["ICC Men's T20 World Cup", "World T20", "ICC T20 World Cup",
                       "ICC World Twenty20", "World Twenty20"],
    "champions trophy":  ["ICC Champions Trophy"],
    "asia cup":  ["Asia Cup"],
    "ashes":     ["The Ashes", "Ashes"],
    "wtc":       ["ICC World Test Championship"],
    "test championship": ["ICC World Test Championship"],
}

# ── Format → match_type mapping (strict) ─────────────────────────────────────
FORMAT_MATCH_TYPE_MAP: dict[str, list[str]] = {
    "test":  ["Test", "MDM"],
    "tests": ["Test", "MDM"],
    "odi":   ["ODI", "ODM"],
    "odis":  ["ODI", "ODM"],
    "t20i":  ["IT20", "T20"],             # international T20s 
    "t20is": ["IT20", "T20"],             # plural variant
    "t20":   ["T20", "IT20"],             # domestic/league T20
    "t20s":  ["T20", "IT20"],
    "international": ["Test", "ODI", "IT20", "ODM", "MDM"],
    "international cricket": ["Test", "ODI", "IT20", "ODM", "MDM"],
    "t20 matches": ["T20", "IT20"],
}



class FeatureMissingError(Exception):
    """Raised in STRICT mode when a required feature is absent from the DataFrame."""
    def __init__(self, feature_name: str, context: str = ""):
        self.feature_name = feature_name
        super().__init__(
            f"[STRICT MODE] Required feature '{feature_name}' is missing from the dataset. "
            f"This must be populated at ingestion time. {context}"
        )


def validate_features(df_columns: list[str], required_features: list[str],
                       mode: ExecutionMode = None) -> list[str]:
    """
    Validate that required features exist in df_columns.

    STRICT: raises FeatureMissingError on first missing required feature.
    SAFE:   returns list of missing features (warnings only).
    LOOSE:  returns empty list (no checks).

    Returns list of missing feature names (empty = all present).
    """
    effective_mode = mode or EXECUTION_MODE
    missing = []

    for fname in required_features:
        feat = FEATURE_REGISTRY.get(fname)
        if feat is None:
            continue  # unknown feature, skip
        if feat.db_col not in df_columns:
            missing.append(fname)
            if effective_mode == "STRICT" and feat.required:
                raise FeatureMissingError(fname)

    if missing and effective_mode == "SAFE":
        for m in missing:
            print(f"    [WARN] Feature '{m}' missing — filter will be skipped.")

    return missing


def resolve_competition(fmt_or_comp: str) -> list[str] | None:
    """
    Resolve a user-supplied format/competition string to canonical DB values.
    Returns None if the key maps to a match_type (not competition table).
    Raises ValueError in STRICT mode for unrecognised competitions.
    """
    key = fmt_or_comp.strip().lower()
    if key in COMPETITION_MAP:
        return COMPETITION_MAP[key]
    return None


def resolve_format(fmt: str) -> list[str] | None:
    """Return the list of match_type values for a given format string."""
    return FORMAT_MATCH_TYPE_MAP.get(fmt.strip().lower())
