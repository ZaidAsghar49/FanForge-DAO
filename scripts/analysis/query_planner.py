"""
query_planner.py — Query Planner Layer
======================================
Transforms a parsed NL query into a deterministic ExecutionPlan.

validate_model.py must ONLY receive ExecutionPlans — it never interprets
raw parsed JSON or comparisons directly.

Architecture:
  NL → ai_parser → parse_claim() → QueryPlanner.build() → ExecutionPlan
                                                               ↓
                                                     validate_model (filter + metric only)
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Any

from scripts.analysis.feature_registry import (
    COMPETITION_MAP, FORMAT_MATCH_TYPE_MAP,
    resolve_competition, resolve_format, EXECUTION_MODE
)

# ── Execution Plan Dataclasses ───────────────────────────────────────────────

@dataclass
class FilterSet:
    """Resolved, normalised filter set ready for execute."""
    # Identity
    subject: str
    subject_col: str          # 'batter' or 'bowler'
    scorecard_aliases: list[str] = field(default_factory=list)
    canonical_name: str = ""

    # Match context
    match_types: list[str] = field(default_factory=list)          # resolved format
    competitions: list[str] = field(default_factory=list)         # resolved competition names
    country: str | None = None
    city: str | None = None
    venue_name: str | None = None
    season_gte: int | None = None    # year >= N
    season_lte: int | None = None    # year <= N
    season_eq: int | None = None     # exact year
    day_night: str | None = None
    innings: int | None = None
    toss_decision: str | None = None
    home_away: str | None = None     # 'Home' | 'Away'

    # Phase / over
    match_phase: str | None = None
    over_number: int | None = None
    over_range: tuple[int, int] | None = None   # (start_0indexed, end_0indexed inclusive)

    # Bowling matchup
    bowler_type: str | None = None
    bowler_hand: str | None = None
    batter_hand: str | None = None   # derived from batter_vs_bowler_type when IS_BOWLING

    # Batting context
    batting_position: int | None = None
    
    # Internal Flags
    _is_t20i: bool = False
    opposition: str | None = None
    ball_type: str | None = None     # 'pink' | 'red'

    # Meta
    is_batting: bool = True


@dataclass
class ExecutionPlan:
    """
    Single execution plan output from QueryPlanner.

    type: 'single' | 'comparison'
    For 'comparison', split_a and split_b hold the two filter sets.
    """
    type: str = "single"                     # 'single' | 'comparison'
    metric: str = ""
    claimed_value: float | None = None
    primary: FilterSet | None = None         # used for 'single'
    split_a: FilterSet | None = None         # used for 'comparison'
    split_b: FilterSet | None = None         # used for 'comparison'
    split_label_a: str = "A"
    split_label_b: str = "B"
    execution_mode: str = EXECUTION_MODE
    raw_filters: dict = field(default_factory=dict)


# ── Comparative Query Detection ──────────────────────────────────────────────

_COMPARISON_KEYWORDS = [
    r"\bcompared to\b", r"\bvs\.?\b", r"\bversus\b",
    r"\bbetter in\b", r"\bworse in\b",
    r"\basia\b.+\boutside\b", r"\boutside\b.+\basia\b",
    r"\bhome\b.+\baway\b", r"\baway\b.+\bhome\b",
]
_ASIA_COUNTRIES = {
    "India", "Pakistan", "Sri Lanka", "Bangladesh",
    "UAE", "Afghanistan", "Nepal",
}

def is_comparative(query: str) -> bool:
    ql = query.lower()
    return any(re.search(p, ql) for p in _COMPARISON_KEYWORDS)


def _detect_split(query: str, base_filters: dict) -> tuple[dict, dict, str, str] | None:
    """
    Returns (filters_A, filters_B, label_A, label_B) for comparative queries.
    Returns None if split cannot be determined.
    """
    ql = query.lower()

    # Asia vs Outside Asia
    if re.search(r"\basia\b", ql):
        fa = {**base_filters, "_region": "Asia"}
        fb = {**base_filters, "_region": "Outside Asia"}
        return fa, fb, "Asia", "Outside Asia"

    # Home vs Away
    if re.search(r"\bhome\b.+\baway\b|\baway\b.+\bhome\b", ql):
        fa = {**base_filters, "home_away": "Home"}
        fb = {**base_filters, "home_away": "Away"}
        return fa, fb, "Home", "Away"

    return None


# ── Season normalisation ─────────────────────────────────────────────────────

def _parse_season(season_str: str) -> tuple[int | None, int | None, int | None]:
    """Returns (season_gte, season_lte, season_eq).
    
    'after 2015' → gte=2016      (exclusive: years strictly after 2015)
    'since 2015' → gte=2015      (inclusive: from 2015 onwards)
    'before 2020' → lte=2019     (exclusive)
    'until 2020'  → lte=2020     (inclusive)
    '2015'        → eq=2015      (exact year)
    """
    s = str(season_str).lower().strip()
    range_m = re.match(r"(\d{4})\s*[-\u2013]\s*(\d{4})", s)
    if range_m:
        return int(range_m.group(1)), int(range_m.group(2)), None

    m = re.search(r"(\d{4})", s)
    if not m:
        return None, None, None
    yr = int(m.group(1))

    if "after" in s:
        return yr + 1, None, None       # "after 2015" → from 2016
    if any(w in s for w in ["since", "onwards"]):
        return yr, None, None            # "since 2015" → from 2015

    if "before" in s or "pre" in s:
        return None, yr - 1, None        # "before 2020" → up to 2019
    if "until" in s:
        return None, yr, None            # "until 2020" → up to 2020

    return None, None, yr                # exact year



# ── Toss decision normalisation ──────────────────────────────────────────────

def _normalise_toss(raw: str) -> str | None:
    t = raw.lower().strip()
    if t in ("bat", "batting", "bat first"):
        return "bat"
    if t in ("field", "bowl", "fielding", "bowling", "bowl first", "field first"):
        return "field"
    return None   # e.g. 'defending' is not a valid toss_decision value


# ── Format / competition resolution ─────────────────────────────────────────

def _resolve_fmt_comp(fmt: str) -> tuple[list[str], list[str]]:
    """Returns (match_types, competitions) for a format/competition string."""
    key = fmt.strip().lower()

    # Is it a league competition?
    comp_vals = resolve_competition(key)
    if comp_vals:
        return [], comp_vals

    # Is it a format (Test/ODI/T20I etc.)?
    mt_vals = resolve_format(key)
    if mt_vals:
        return mt_vals, []

    # Fallback: treat as raw match_type token
    return [fmt], []


# ── FilterSet builder ────────────────────────────────────────────────────────

def _build_filter_set(
    subject: str,
    is_batting: bool,
    raw_filters: dict,
) -> FilterSet:
    """
    Convert raw parsed filter dict into a normalised FilterSet.
    All lookups are deterministic — no fuzzy logic here.
    """
    fs = FilterSet(
        subject=subject,
        subject_col="batter" if is_batting else "bowler",
        is_batting=is_batting,
    )

    # Format / competition
    fmt = raw_filters.get("format")
    if fmt:
        mt, comp = _resolve_fmt_comp(fmt)
        fs.match_types = mt
        fs.competitions = comp
        if fmt.strip().lower() in ["t20i", "t20is", "it20"]:
            fs._is_t20i = True

    # Series (may override or add competition)
    series = raw_filters.get("series")
    if series:
        comp_vals = resolve_competition(series.lower())
        if comp_vals:
            fs.competitions += [c for c in comp_vals if c not in fs.competitions]

    # Location
    fs.country    = raw_filters.get("country")
    fs.city       = raw_filters.get("city")
    fs.venue_name = raw_filters.get("venue_name")

    # Season
    season_raw = raw_filters.get("season")
    if season_raw:
        fs.season_gte, fs.season_lte, fs.season_eq = _parse_season(season_raw)

    # Match context
    fs.day_night     = raw_filters.get("day_night")
    fs.innings       = raw_filters.get("innings")
    fs.match_phase   = raw_filters.get("match_phase")
    fs.over_number   = raw_filters.get("over_number")
    # over_range: list [start, end] from parser -> stored as tuple on FilterSet
    _or = raw_filters.get("over_range")
    if _or and len(_or) == 2:
        fs.over_range = (int(_or[0]), int(_or[1]))
    fs.batting_position = raw_filters.get("batting_position")
    fs.opposition    = raw_filters.get("opposition")
    fs.home_away     = raw_filters.get("home_away")

    # Toss
    td = raw_filters.get("toss_decision")
    if td:
        fs.toss_decision = _normalise_toss(td)   # None if unmappable

    # Bowler matchup (bowling queries)
    fs.bowler_type = raw_filters.get("bowler_type")
    fs.bowler_hand = raw_filters.get("bowler_hand")

    # batter_vs_bowler_type — behaves differently depending on role
    bvbt = raw_filters.get("batter_vs_bowler_type")
    if bvbt:
        bvbt_l = bvbt.lower()
        if not is_batting:
            # We're a Bowler → batter_hand filter
            if "left" in bvbt_l:
                fs.batter_hand = "left"
            elif "right" in bvbt_l:
                fs.batter_hand = "right"
        else:
            # We're a Batter → bowler_type filter (already set above usually)
            if "spin" in bvbt_l:
                fs.bowler_type = "Spin"
            elif "pace" in bvbt_l:
                fs.bowler_type = "Pace"

    # Ball type (pink ball)
    day_night_val = (raw_filters.get("day_night") or "").lower()
    if "pink" in str(raw_filters).lower() or "day/night" in day_night_val or "day-night" in day_night_val:
        fs.ball_type = "pink"

    # Region filter (comparative)
    region = raw_filters.get("_region")
    if region == "Asia":
        fs.country = None  # handled specially in engine
        fs._asia_filter = True
    elif region == "Outside Asia":
        fs.country = None
        fs._non_asia_filter = True

    return fs


# ── Main QueryPlanner ────────────────────────────────────────────────────────

class QueryPlanner:
    """
    Converts parsed NL output into a deterministic ExecutionPlan.

    Usage:
        plan = QueryPlanner().build(parsed, canonical_name, subj_res, metric)
    """

    def build(
        self,
        parsed: dict,
        canonical_name: str,
        subj_res: dict,
        metric: str,
        query_string: str = "",
    ) -> ExecutionPlan:
        raw_filters = parsed.get("filters", {}) or {}
        claimed_val = parsed.get("claimed_value")

        # Determine query role
        is_batting = self._resolve_role(metric, subj_res)

        plan = ExecutionPlan(
            metric=metric,
            claimed_value=claimed_val,
            execution_mode=EXECUTION_MODE,
            raw_filters=raw_filters,
        )

        # ── Comparative? ──────────────────────────────────────────────────────
        if is_comparative(query_string):
            split = _detect_split(query_string, raw_filters)
            if split:
                fa_raw, fb_raw, label_a, label_b = split
                plan.type = "comparison"
                plan.split_label_a = label_a
                plan.split_label_b = label_b

                plan.split_a = _build_filter_set(canonical_name, is_batting, fa_raw)
                plan.split_a.canonical_name = canonical_name

                plan.split_b = _build_filter_set(canonical_name, is_batting, fb_raw)
                plan.split_b.canonical_name = canonical_name
                return plan

        # ── Single query ──────────────────────────────────────────────────────
        plan.type = "single"
        fs = _build_filter_set(canonical_name, is_batting, raw_filters)
        fs.canonical_name = canonical_name
        plan.primary = fs
        return plan

    # ── Role resolution ───────────────────────────────────────────────────────
    _BOWLING_KEYWORDS = {
        "wickets", "economy rate", "economy", "dots forced", "extras conceded",
        "extras", "runs conceded", "bowling average", "bowling strike rate",
        "bowling economy", "strike rate",
    }
    _BATTING_KEYWORDS = {
        "high score", "milestones", "partnership runs", "balls faced",
        "batting average", "batting strike rate", "boundary %", "dot ball %",
        "total runs", "runs scored",
    }

    def _resolve_role(self, metric: str, subj_res: dict) -> bool:
        """True = batting, False = bowling."""
        ml = (metric or "").lower()
        if any(k in ml for k in self._BOWLING_KEYWORDS):
            return False
        if any(k in ml for k in self._BATTING_KEYWORDS):
            return True
        # Ambiguous → use player's primary role
        role = subj_res.get("primary_role", "Unknown")
        return "Bowler" not in role
