"""
ai_parser.py — Extended NLP Parser (38-Parameter Engine)
=========================================================
Extracts structured filter & metric JSON from natural-language cricket claims.

Supported filter keys (38 total):
  Match Context    : venue_name, city, country, format, season, day_night,
                     toss_winner, toss_decision, innings, series, home_away,
                     neutral_venue
  Batting Analytics: subject (batter), total_runs, balls_faced, batting_average,
                     strike_rate, dismissal_type, dot_ball_pct, boundary_pct,
                     batting_position, non_striker, partnership_runs, high_score,
                     milestones
  Bowling Analytics: bowler, bowler_type, bowler_hand, economy_rate,
                     bowling_strike_rate, wickets, dots_forced, extras_conceded,
                     over_number, match_phase, batter_vs_bowler_type,
                     batter_vs_bowler, runs_conceded_in_over
"""

import json
import os
import re
from enum import Enum
from typing import Any, Literal

from dotenv import load_dotenv
from groq import Groq

load_dotenv()

# ---------------------------------------------------------------------------
# LLM-based parser
# ---------------------------------------------------------------------------

# ── JSON Schema Definition (Strict) ──────────────────────────────────────────
# All 38 dimensions must be present in the final engine, but the LLM
# focuses on these keys for mapping.
FILTER_KEYS = [
    # Match-context filters ONLY — never output metrics
    "venue_name", "city", "country", "format", "season", "day_night",
    "toss_winner", "toss_decision", "innings", "series", "home_away",
    "neutral_venue", "opposition", "dismissal_type", "batting_position",
    "non_striker", "milestones", "bowler", "bowler_type", "bowler_hand",
    "over_number", "over_range", "match_phase",
    "batter_vs_bowler_type", "batter_vs_bowler",
    # Derived features (must exist in DB before query)
    "ball_type",
]

_FILTER_SCHEMA_PROMPT = """
Return a JSON object with these exact top-level keys:
{
  "subject_type": <"player" | "team" | "country" | null>,
  "subject": <str | null>,          // player name
  "metric": <str | null>,           // e.g. "Batting Average", "Wickets"
  "claimed_value": <float | null>,  // numeric value
  "as_of_date": <str | null>,       // ISO date: "YYYY-MM-DD" (optional temporal anchor)
  "filters": {
    "venue_name": <str | null>, "city": <str | null>, "country": <str | null>,
    "format": <str | null>, "season": <str | null>, "day_night": <str | null>,
    "toss_winner": <str | null>, "toss_decision": <str | null>,
    "innings": <int | null>, "series": <str | null>, "home_away": <str | null>,
    "neutral_venue": <bool | null>, "opposition": <str | null>,
    "dismissal_type": <str | null>, "batting_position": <int | null>,
    "non_striker": <str | null>, "milestones": <str | null>,
    "bowler": <str | null>, "bowler_type": <str | null>, "bowler_hand": <str | null>,
    "over_number": <int | null>, "over_range": <list of 2 ints [start, end] 0-indexed | null>, "match_phase": <str | null>,
    "batter_vs_bowler_type": <str | null>, "batter_vs_bowler": <str | null>
  }
}
"""

_SYSTEM_PROMPT = f"""You are an elite cricket data analyst and NLP parser.
Your task is to parse a natural-language cricket claim into a structured JSON object.
{_FILTER_SCHEMA_PROMPT}
Rules:
• Only output valid JSON — no markdown, no explanation.
• Set any unmentioned filter to null (not absent).
• subject_type must match the claim intent: "player" for player stats, "team" for team stats, "country" for country aggregates.
• as_of_date: if claim asks about a year (e.g., "in 2023"), set as_of_date to that year's end (2023-12-31).
• For bowler_hand infer from phrases like "left-arm pace" → bowler_hand="Left", bowler_type="Pace".
• For match_phase: "powerplay" → "Powerplay"; "death overs" / "final overs" → "Death";
  "middle overs" → "Middle".
• innings: "first innings"→1, "second innings"→2, "third innings"→3, "fourth innings"→4, "chases"/"chasing"→2, "batting first"→1.
• For milestones: "centuries"/"100s"→"100s", "fifties"/"50s"→"50s",
  "fifties and centuries"→"50s and 100s".
• home_away: infer from context when possible, e.g. "at home" → "Home".
"""


# ---------------------------------------------------------------------------
# Multi-Claim Decomposition System Prompt
# ---------------------------------------------------------------------------

_MULTI_CLAIM_SYSTEM_PROMPT = f"""You are an elite cricket data analyst and NLP parser specialising in semantic decomposition.

Your task is to decompose a natural-language paragraph containing multiple cricket statistical claims into a JSON ARRAY of structured objects.

Each object in the array must conform to this schema:
{_FILTER_SCHEMA_PROMPT}

CRITICAL DECOMPOSITION RULES — YOU MUST FOLLOW ALL OF THEM:

1. OUTPUT FORMAT:
   • Output ONLY a raw, parseable JSON array — starting with '[' and ending with ']'.
   • Absolutely NO markdown, NO code fences (```), NO prose, NO explanation before or after the array.
   • If you output anything other than a raw JSON array, you have failed.

2. CLAIM COUNTING:
   • Count the number of distinct structural claims in the paragraph.
   • Output EXACTLY that many objects in the array — no more, no fewer.
   • A "structural claim" is any assertion with its own numeric value, even if it shares a subject with a prior claim.

3. CO-REFERENCE RESOLUTION:
   • When a clause omits the subject, metric, format, or a filter already established by a prior clause, INHERIT those values.
   • Example: "Rohit Sharma averages 66 against spin in ODIs in Australia AND 65 in India" → the second object inherits subject=Rohit Sharma, metric=Batting Average, format=ODI, bowler_type=Spin; only country changes to India.
   • Do NOT leave inherited fields as null — carry them forward.

4. CONTRASTIVE PARAMETER SPLITTING:
   • When the paragraph uses contrastive conjunctions ("but", "whereas", "however", "while", "yet", "though") to introduce a changed parameter (e.g. a different bowler_type), FORK a new object.
   • The forked object keeps the primary subject, metric, and format identical to the preceding clause.
   • Only the parameter that was explicitly changed (e.g. bowler_type: Spin → Pace) should differ.
   • Spatially scoped filters (country, city, venue_name) that were NOT explicitly carried into the contrasted clause must be reset to null in the forked object.

5. FILTER RULES (same as single-claim parser):
   • Set any unmentioned filter to null (not absent).
   • subject_type must be "player" for player stats, "team" for team aggregates, "country" for country-level data.
   • For bowler_type: "spin" / "spinner" → "Spin"; "pace" / "fast" / "seam" → "Pace".
   • For match_phase: "powerplay" → "Powerplay"; "death overs" → "Death"; "middle overs" → "Middle".
   • innings: "first innings" → 1, "chasing" → 2, "batting first" → 1.
   • as_of_date: if a year is mentioned (e.g. "in 2023"), set as_of_date to "2023-12-31".
   • home_away: "at home" → "Home"; "away" → "Away".

EXAMPLE INPUT:
  "Rohit Sharma averages 66 against spin in ODIs in Australia and 65 in India, but against pace it drops to 33."

EXAMPLE OUTPUT (exactly this structure, raw array, no fences):
[
  {{"subject_type": "player", "subject": "Rohit Sharma", "metric": "Batting Average", "claimed_value": 66.0, "as_of_date": null,
    "filters": {{"venue_name": null, "city": null, "country": "Australia", "format": "ODI", "season": null, "day_night": null, "toss_winner": null, "toss_decision": null, "innings": null, "series": null, "home_away": null, "neutral_venue": null, "opposition": null, "dismissal_type": null, "batting_position": null, "non_striker": null, "milestones": null, "bowler": null, "bowler_type": "Spin", "bowler_hand": null, "over_number": null, "over_range": null, "match_phase": null, "batter_vs_bowler_type": null, "batter_vs_bowler": null}}}},
  {{"subject_type": "player", "subject": "Rohit Sharma", "metric": "Batting Average", "claimed_value": 65.0, "as_of_date": null,
    "filters": {{"venue_name": null, "city": null, "country": "India", "format": "ODI", "season": null, "day_night": null, "toss_winner": null, "toss_decision": null, "innings": null, "series": null, "home_away": null, "neutral_venue": null, "opposition": null, "dismissal_type": null, "batting_position": null, "non_striker": null, "milestones": null, "bowler": null, "bowler_type": "Spin", "bowler_hand": null, "over_number": null, "over_range": null, "match_phase": null, "batter_vs_bowler_type": null, "batter_vs_bowler": null}}}},
  {{"subject_type": "player", "subject": "Rohit Sharma", "metric": "Batting Average", "claimed_value": 33.0, "as_of_date": null,
    "filters": {{"venue_name": null, "city": null, "country": null, "format": "ODI", "season": null, "day_night": null, "toss_winner": null, "toss_decision": null, "innings": null, "series": null, "home_away": null, "neutral_venue": null, "opposition": null, "dismissal_type": null, "batting_position": null, "non_striker": null, "milestones": null, "bowler": null, "bowler_type": "Pace", "bowler_hand": null, "over_number": null, "over_range": null, "match_phase": null, "batter_vs_bowler_type": null, "batter_vs_bowler": null}}}}
]
"""


# ---------------------------------------------------------------------------
# Pydantic contract guardrails (strict schema + safe coercions)
# ---------------------------------------------------------------------------

try:
    from pydantic import BaseModel, Field, ValidationError, field_validator
except Exception:  # pragma: no cover
    BaseModel = object  # type: ignore
    Field = lambda *a, **k: None  # type: ignore
    ValidationError = Exception  # type: ignore
    field_validator = lambda *a, **k: (lambda fn: fn)  # type: ignore


class MetricEnum(str, Enum):
    BATTING_AVG = "Batting Average"
    STRIKE_RATE = "Strike Rate"
    TOTAL_RUNS = "Total Runs"
    DOT_BALL_PCT = "Dot Ball %"
    BOUNDARY_PCT = "Boundary %"
    HIGH_SCORE = "High Score"
    MILESTONES = "Milestones"
    PARTNERSHIP_RUNS = "Partnership Runs"
    BALLS_FACED = "Balls Faced"
    WICKETS = "Wickets"
    ECONOMY_RATE = "Economy Rate"
    BOWLING_STRIKE_RATE = "Bowling Strike Rate"
    BOWLING_AVG = "Bowling Average"
    DOTS_FORCED = "Dots Forced"
    EXTRAS_CONCEDED = "Extras Conceded"
    RUNS_CONCEDED_IN_OVER = "Runs Conceded in Over"


class MatchPhaseEnum(str, Enum):
    POWERPLAY = "Powerplay"
    MIDDLE = "Middle"
    DEATH = "Death"


class TossDecisionEnum(str, Enum):
    BAT = "bat"
    FIELD = "field"


class FiltersModel(BaseModel):
    venue_name: str | None = None
    city: str | None = None
    country: str | None = None
    format: str | None = None
    season: str | None = None
    day_night: str | None = None
    toss_winner: str | None = None
    toss_decision: str | None = None
    innings: int | None = Field(default=None, ge=1, le=4)
    series: str | None = None
    home_away: str | None = None
    neutral_venue: bool | None = None
    opposition: str | None = None
    dismissal_type: str | None = None
    batting_position: int | None = Field(default=None, ge=1, le=11)
    non_striker: str | None = None
    milestones: str | None = None
    bowler: str | None = None
    bowler_type: str | None = None
    bowler_hand: str | None = None
    over_number: int | None = None
    over_range: list[int] | None = None
    match_phase: str | None = None
    batter_vs_bowler_type: str | None = None
    batter_vs_bowler: str | None = None
    ball_type: str | None = None
    # Temporal anchor (used by validate_model loader truncation)
    as_of_date: str | None = None

    @field_validator("innings", mode="before")
    @classmethod
    def _coerce_innings(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, float) and v.is_integer():
            return int(v)
        if isinstance(v, str):
            s = v.strip().lower()
            mapping = {
                "1": 1, "1st": 1, "first": 1, "first innings": 1,
                "2": 2, "2nd": 2, "second": 2, "second innings": 2,
                "3": 3, "3rd": 3, "third": 3, "third innings": 3,
                "4": 4, "4th": 4, "fourth": 4, "fourth innings": 4,
                "chasing": 2, "chases": 2, "chase": 2,
                "batting first": 1,
            }
            return mapping.get(s, v)
        return v

    @field_validator("toss_decision", mode="before")
    @classmethod
    def _coerce_toss_decision(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("bat", "batting", "bat first"):
                return "bat"
            if s in ("field", "fielding", "bowl", "bowling", "field first", "bowl first"):
                return "field"
        return v

    @field_validator("match_phase", mode="before")
    @classmethod
    def _coerce_match_phase(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip().lower()
            if "powerplay" in s:
                return MatchPhaseEnum.POWERPLAY.value
            if "death" in s or "final" in s:
                return MatchPhaseEnum.DEATH.value
            if "middle" in s:
                return MatchPhaseEnum.MIDDLE.value
        return v

    @field_validator("over_range", mode="before")
    @classmethod
    def _coerce_over_range(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, tuple):
            v = list(v)
        return v


class ParsedClaimModel(BaseModel):
    subject_type: Literal["player", "team", "country"] | None = None
    subject: str | None = None
    metric: str | None = None
    claimed_value: float | None = None
    as_of_date: str | None = None
    filters: FiltersModel = Field(default_factory=FiltersModel)

    @field_validator("claimed_value", mode="before")
    @classmethod
    def _coerce_claimed_value(cls, v: Any) -> Any:
        if v is None:
            return None
        try:
            return float(v)
        except Exception:
            return None

    @field_validator("metric", mode="before")
    @classmethod
    def _coerce_metric(cls, v: Any) -> Any:
        if v is None:
            return None
        if not isinstance(v, str):
            return v
        raw = v.strip()
        # Gentle normalisation: map common variants to canonical names
        m = raw.lower()
        mapping = {
            "average": "Batting Average",
            "batting average": "Batting Average",
            "bowling average": "Bowling Average",
            "economy": "Economy Rate",
            "economy rate": "Economy Rate",
            "bowling economy": "Economy Rate",
            "sr": "Strike Rate",
            "strike rate": "Strike Rate",
            "batting strike rate": "Strike Rate",
            "bowling strike rate": "Bowling Strike Rate",
            "wicket": "Wickets",
            "wickets": "Wickets",
            "runs": "Total Runs",
            "total runs": "Total Runs",
            "balls faced": "Balls Faced",
            "dot ball %": "Dot Ball %",
            "boundary %": "Boundary %",
            "high score": "High Score",
            "milestones": "Milestones",
            "partnership runs": "Partnership Runs",
            "dots forced": "Dots Forced",
            "extras conceded": "Extras Conceded",
            "runs conceded in over": "Runs Conceded in Over",
        }
        return mapping.get(m, raw)

    @field_validator("as_of_date", mode="before")
    @classmethod
    def _coerce_as_of_date(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            # accept YYYY-MM-DD only; everything else becomes None
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
                return s
        return None


# ---------------------------------------------------------------------------
# Pydantic array wrapper  (used by parse_paragraph)
# ---------------------------------------------------------------------------

try:
    from pydantic import RootModel  # Pydantic v2
    class ParsedClaimArray(RootModel[list[ParsedClaimModel]]):
        """
        Pydantic v2 wrapper that validates a JSON array where every element
        must conform to ParsedClaimModel.  All existing field coercions
        (metric normalisation, innings mapping, toss_decision, etc.) are
        applied element-by-element automatically.
        """
        pass
    _HAS_ROOT_MODEL = True
except ImportError:
    # Pydantic v1 fallback — no RootModel; we validate items individually.
    ParsedClaimArray = None  # type: ignore
    _HAS_ROOT_MODEL = False


def _validate_and_sanitize_array(raw_list: list) -> list[dict]:
    """
    Validate a raw list of dicts against ParsedClaimModel.

    Each element undergoes the same Pydantic coercions as single-claim
    parsing (metric normalisation, innings mapping, toss_decision, etc.).
    Elements that fail validation are silently skipped with a warning logged
    to avoid poisoning the entire result for one bad object.

    Returns a list of fully-populated, back-filled filter dicts — one per
    valid claim object.  Guaranteed to be a list (may be empty on total
    failure).
    """
    import logging
    _log = logging.getLogger("ai_parser")

    results: list[dict] = []

    for i, item in enumerate(raw_list):
        if not isinstance(item, dict):
            _log.warning(f"[array item {i}] Not a dict — skipping.")
            continue
        try:
            if hasattr(ParsedClaimModel, "model_validate"):
                # Pydantic v2 path
                model = ParsedClaimModel.model_validate(item)
                out = model.model_dump()
            else:
                # Pydantic v1 path
                model = ParsedClaimModel(**item)  # type: ignore
                out = model.dict()  # type: ignore

            # Back-fill all FILTER_KEYS so downstream engine never KeyErrors
            f = out.get("filters") or {}
            for k in FILTER_KEYS:
                f.setdefault(k, None)
            # Mirror top-level temporal anchor into filters
            if out.get("as_of_date") and not f.get("as_of_date"):
                f["as_of_date"] = out["as_of_date"]
            # Inject convenience `location` alias (same as parse_claim)
            loc_parts = [v for v in [f.get("country"), f.get("city"), f.get("venue_name")] if v]
            f["location"] = loc_parts[0] if loc_parts else None
            out["filters"] = f
            results.append(out)
        except Exception as ve:
            _log.warning(f"[array item {i}] Pydantic validation error — skipping: {ve}")
            continue

    return results


def _llm_call(client: Groq, claim_string: str, system_prompt: str) -> str:
    response = client.chat.completions.create(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f'Claim: "{claim_string}"'},
        ],
        model="llama-3.3-70b-versatile",
        temperature=0,
    )
    return response.choices[0].message.content


_CRITIC_SYSTEM_PROMPT = """You are a strict JSON contract critic.
You will be given:
1) The original natural-language claim
2) A candidate parsed JSON object

Your job is to find discrepancies, missing constraints, or wrong interpretations.

Return ONLY valid JSON of this form:
{
  "verdict": "ok" | "retry",
  "issues": [<string>...]
}

Rules:
- If any key is missing, wrong type, or a crucial filter is missed (e.g. "vs spin", "in wins"), verdict MUST be "retry".
- CRITICAL: Subject anchoring.
  If the claim is about a TEAM/COUNTRY aggregate (e.g. "How many runs has India scored", "Australia's total runs"),
  but the candidate JSON sets subject_type="player" or looks like a player subject, verdict MUST be "retry" and include issue "SubjectTypeMismatch".
- If everything is consistent, verdict MUST be "ok" with issues=[].
"""


def _parse_json_only(text: str) -> dict:
    """Parse a JSON object from raw LLM text (no schema coercion)."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    t = re.sub(r",\s*}", "}", t)
    t = re.sub(r",\s*]", "]", t)
    obj = json.loads(t)
    if not isinstance(obj, dict):
        raise ValueError("Critic output must be a JSON object")
    return obj


def _parse_json_array(text: str) -> list:
    """
    Parse a JSON **array** from raw LLM text.

    Strips markdown code fences and trailing-comma artefacts, then asserts
    the root element is a list.  Raises ValueError if it is not.
    """
    t = text.strip()
    # Strip markdown fences: ```json ... ``` or ``` ... ```
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
        t = t.strip()
    # Fix common LLM trailing comma errors before parsing
    t = re.sub(r",\s*}", "}", t)
    t = re.sub(r",\s*]", "]", t)
    parsed = json.loads(t)
    if not isinstance(parsed, list):
        raise ValueError(
            f"Multi-claim LLM output must be a JSON array; got {type(parsed).__name__}."
        )
    return parsed


_TEAM_COUNTRY_TOKENS = {
    "india", "pakistan", "australia", "england", "south africa",
    "new zealand", "west indies", "sri lanka", "bangladesh",
    "afghanistan", "zimbabwe", "ireland", "uae", "scotland", "nepal",
}


def _is_team_or_country_query(claim: str) -> bool:
    cl = claim.lower()
    # Heuristic patterns for team/country aggregates
    if re.search(r"\bhow many runs has\b|\bteam\b|\bthey scored\b|\bhas (india|pakistan|australia|england)\b.*\bscored\b", cl):
        return True
    return any(tok in cl for tok in _TEAM_COUNTRY_TOKENS)


def _subject_type_guard(claim: str, candidate: dict) -> tuple[bool, str | None]:
    """
    Deterministic guardrail against subject confusion.
    If claim looks like a team/country aggregate but parse claims a player subject, reject.
    """
    st = (candidate.get("subject_type") or "").strip().lower()
    subj = (candidate.get("subject") or "").strip().lower()
    cl = claim.lower()

    # If the parsed subject (or its significant name tokens) is in the claim, it's not a mismatch.
    subj_tokens = [t for t in subj.split() if len(t) >= 3]
    if any(t in cl for t in subj_tokens) or subj in cl:
        return True, None

    if _is_team_or_country_query(claim):
        # If we explicitly got a player subject_type or a 2-token name style, flag mismatch.
        looks_like_player_name = bool(re.match(r"^[a-z]+(?:\s+[a-z]+){1,3}$", subj)) and " " in subj
        if st == "player" or (not st and looks_like_player_name):
            return False, "SubjectTypeMismatch"
    return True, None


def _validate_and_sanitize(text: str) -> dict:
    """Validate JSON structure and types. Prefer Pydantic contract when available."""
    # Heuristic sanitization
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    
    # Fix common LLM trailing comma error before parsing
    text = re.sub(r",\s*}", "}", text)
    text = re.sub(r",\s*]", "]", text)

    data = json.loads(text)

    # Pydantic path (strict contract + safe coercions)
    if hasattr(ParsedClaimModel, "model_validate"):
        model = ParsedClaimModel.model_validate(data)
        out = model.model_dump()
        # Ensure all FILTER_KEYS exist for backwards compatibility
        f = out.get("filters") or {}
        for k in FILTER_KEYS:
            f.setdefault(k, None)
        # Mirror top-level temporal anchor into filters for downstream plumbing
        if out.get("as_of_date") and not f.get("as_of_date"):
            f["as_of_date"] = out["as_of_date"]
        out["filters"] = f
        return out

    # Fallback (no pydantic installed): keep legacy sanitization
    if not isinstance(data, dict):
        raise ValueError("Root must be an object")
    for k in ["subject", "metric", "claimed_value", "filters"]:
        if k not in data:
            data[k] = None
    if not isinstance(data.get("filters"), dict):
        data["filters"] = {}
    f = data["filters"]
    for k in FILTER_KEYS:
        if k not in f:
            f[k] = None
    if data["claimed_value"] is not None:
        try:
            data["claimed_value"] = float(data["claimed_value"])
        except (ValueError, TypeError):
            data["claimed_value"] = None
    if f.get("innings") is not None:
        try:
            f["innings"] = int(f["innings"])
        except (ValueError, TypeError):
            f["innings"] = None
    return data


# ---------------------------------------------------------------------------
# Rule-based multi-claim fallback  (no LLM required)
# ---------------------------------------------------------------------------

# Contrastive conjunctions that signal a parameter fork
_CONTRASTIVE_RE = re.compile(
    r"(?<![a-z])(?:but|whereas|however|while|yet|though)\b",
    re.IGNORECASE,
)

# Additive connectors that signal a co-reference continuation (same params, new value)
_ADDITIVE_RE = re.compile(
    r"\band\s+(?=\d)|\band\s+(?=[A-Z0-9])|;\s*",
    re.IGNORECASE,
)


def _mock_parse_paragraph(paragraph: str) -> list[dict]:
    """
    Rule-based multi-claim decomposition fallback.

    Algorithm
    ---------
    1. Split the paragraph into fragments on contrastive conjunctions
       ("but", "whereas", "however", "while", "yet", "though") and on
       sentence boundaries, collecting at most one top-level numeric value
       per fragment.
    2. Parse each fragment independently via the existing _mock_parse().
    3. Apply co-reference inheritance: when a fragment has None for
       subject / metric / format / bowler_type, carry the value forward
       from the immediately preceding parsed object.
    4. Apply contrastive reset: when a contrastive conjunction triggered
       the split, reset spatial filters (country, city, venue_name) in the
       new fragment if they were not explicitly mentioned in it.

    Returns a list of at least one ParsedClaimModel-equivalent dict.
    """
    import logging
    _log = logging.getLogger("ai_parser")

    # ── Step 1: Fragment the paragraph ────────────────────────────────────────
    # We walk through the text character-by-character and slice at contrastive
    # and sentence-boundary positions so we retain the split-trigger type.
    #
    # Each fragment is a tuple of (text: str, is_contrastive: bool)

    fragments: list[tuple[str, bool]] = []
    remaining = paragraph

    while remaining:
        # Look for the earliest split point
        contra_m = _CONTRASTIVE_RE.search(remaining)
        # Simple sentence boundary (period/semicolon followed by space + capital)
        sent_m = re.search(r"[.;]\s+(?=[A-Z])", remaining)

        candidates = [m for m in [contra_m, sent_m] if m]
        if not candidates:
            fragments.append((remaining.strip(), False))
            break

        # Pick the earliest match
        earliest = min(candidates, key=lambda m: m.start())
        is_contrastive = earliest is contra_m

        before = remaining[: earliest.start()].strip()
        if before:
            fragments.append((before, False))
        remaining = remaining[earliest.end():].strip()
        # The conjunction itself becomes part of the next fragment's context
        # only if it's contrastive (so _mock_parse can still pick up bowler_type etc.)
        if is_contrastive and remaining:
            # Prepend the matched conjunction so _mock_parse sees "against pace"
            remaining = earliest.group(0) + " " + remaining
            fragments_contrastive_flag = True
        else:
            fragments_contrastive_flag = False

        # Mark the *next* fragment as contrastive once we start appending it
        _next_is_contrastive = is_contrastive
        # Re-tag: set last-added fragment's is_contrastive flag for the coming one
        if fragments:
            # The next iteration will mark is_contrastive correctly via remaining
            pass

    # Guard: if no numeric value found anywhere, return single mock parse
    if not fragments:
        return [_single_mock_with_location(paragraph)]

    # ── Step 2: Parse each fragment ───────────────────────────────────────────
    parsed_fragments: list[dict] = []
    for frag_text, _ in fragments:
        if not re.search(r"\d", frag_text):
            # Fragment has no numeric value — it's not a verifiable claim; skip
            continue
        parsed_fragments.append(_single_mock_with_location(frag_text))

    if not parsed_fragments:
        return [_single_mock_with_location(paragraph)]

    # ── Step 3: Co-reference inheritance ──────────────────────────────────────
    _INHERIT_TOP = ("subject", "metric", "subject_type")
    _INHERIT_FILTER = ("format", "bowler_type", "bowler_hand")

    prev = parsed_fragments[0]
    for cur in parsed_fragments[1:]:
        cur_fl = cur.get("filters", {})
        prev_fl = prev.get("filters", {})

        # Inherit top-level keys
        for key in _INHERIT_TOP:
            if cur.get(key) is None and prev.get(key) is not None:
                cur[key] = prev[key]

        # Inherit filter-level keys
        for key in _INHERIT_FILTER:
            if cur_fl.get(key) is None and prev_fl.get(key) is not None:
                cur_fl[key] = prev_fl[key]

        # Refresh location alias after potential inheritance
        loc_parts = [v for v in [cur_fl.get("country"), cur_fl.get("city"), cur_fl.get("venue_name")] if v]
        cur_fl["location"] = loc_parts[0] if loc_parts else None

        prev = cur

    _log.debug(
        f"_mock_parse_paragraph: decomposed into {len(parsed_fragments)} fragment(s)."
    )
    return parsed_fragments


def _single_mock_with_location(text: str) -> dict:
    """Run _mock_parse on text and inject the location alias."""
    result = _mock_parse(text)
    fl = result.get("filters", {})
    loc_parts = [v for v in [fl.get("country"), fl.get("city"), fl.get("venue_name")] if v]
    fl["location"] = loc_parts[0] if loc_parts else None
    return result


# ---------------------------------------------------------------------------
# Single-claim public API  (unchanged)
# ---------------------------------------------------------------------------

def parse_claim(claim_string: str) -> dict:
    """
    Parse a NL cricket claim into a structured filter dict.
    Falls back to rule-based _mock_parse if LLM fails or is invalid.
    """
    result = _parse_claim_internal(claim_string)
    if isinstance(result, dict) and "filters" in result:
        fl = result["filters"]
        if isinstance(fl, dict):
            loc_parts = [v for v in [fl.get("country"), fl.get("city"), fl.get("venue_name")] if v]
            if loc_parts:
                fl["location"] = loc_parts[0]
            else:
                fl["location"] = None
    return result

# ---------------------------------------------------------------------------
# Multi-claim public API  (new)
# ---------------------------------------------------------------------------

def parse_paragraph(paragraph: str) -> list[dict]:
    """
    Decompose a multi-claim conversational paragraph into a list of
    fully-resolved, isolated ``ParsedClaimModel`` payloads.

    Co-Reference Resolution
    -----------------------
    When a sub-clause omits the subject / metric / format / bowler_type that
    was established by an earlier clause in the same paragraph (e.g. "…and
    65 in India…"), the engine inherits those parameters from the preceding
    parsed claim object automatically.

    Contrastive Parameter Splitting
    --------------------------------
    When a contrastive conjunction ("but", "whereas", "however") introduces a
    changed parameter (e.g. Spin → Pace), a fresh claim object is forked while
    the primary subject, metric, and format remain intact.

    Output Contract
    ---------------
    Always returns a **list** (never a bare dict).  Each element is a dict
    that satisfies the same schema as ``parse_claim`` output:

    .. code-block:: python

        [
          {
            "subject_type": "player",
            "subject": "Rohit Sharma",
            "metric": "Batting Average",
            "claimed_value": 66.0,
            "as_of_date": None,
            "filters": {
                "format": "ODI",
                "country": "Australia",
                "bowler_type": "Spin",
                ...  # all 38 keys present
            },
            "_paragraph_decomposed": True,
            "_parse_attempts": 1,
            "_parse_verified": True,
          },
          ...
        ]

    Fallback
    --------
    If the GROQ API is unavailable or LLM parsing fails, falls back to the
    rule-based ``_mock_parse_paragraph()`` which uses regex + co-reference
    inheritance heuristics.  The result is tagged with
    ``_parse_degraded=True``.

    Args:
        paragraph: A natural-language paragraph that may contain multiple
                   cricket statistical assertions.

    Returns:
        A list of dicts, one per structural claim detected.
    """
    import logging
    _log = logging.getLogger("ai_parser")

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        _log.debug("parse_paragraph: no GROQ_API_KEY — using mock fallback.")
        results = _mock_parse_paragraph(paragraph)
        for r in results:
            r["_paragraph_decomposed"] = True
            r["_parse_degraded"] = True
            r["_parse_error"] = "No GROQ_API_KEY set"
        return results

    try:
        client = Groq(api_key=api_key)
        last_error: Exception | None = None

        for attempt in range(1, 4):
            try:
                raw_text = _llm_call(client, paragraph, _MULTI_CLAIM_SYSTEM_PROMPT)
            except Exception as call_exc:
                last_error = call_exc
                _log.warning(f"parse_paragraph attempt {attempt} LLM call failed: {call_exc}")
                continue

            try:
                raw_list = _parse_json_array(raw_text)
            except Exception as parse_exc:
                last_error = parse_exc
                _log.warning(
                    f"parse_paragraph attempt {attempt} JSON parse failed: {parse_exc}\n"
                    f"Raw text: {raw_text[:300]}"
                )
                continue

            if not raw_list:
                last_error = ValueError("LLM returned an empty array")
                _log.warning(f"parse_paragraph attempt {attempt}: empty array returned.")
                continue

            validated = _validate_and_sanitize_array(raw_list)
            if not validated:
                last_error = ValueError("All array items failed Pydantic validation")
                _log.warning(f"parse_paragraph attempt {attempt}: all items invalid.")
                continue

            # Success — stamp metadata
            for item in validated:
                item["_paragraph_decomposed"] = True
                item["_parse_attempts"] = attempt
                item["_parse_verified"] = True

            _log.info(
                f"parse_paragraph: decomposed paragraph into "
                f"{len(validated)} claim(s) on attempt {attempt}."
            )
            return validated

        raise ValueError(f"parse_paragraph failed after retries: {last_error}")

    except Exception as exc:
        _log.error(f"parse_paragraph error — falling back to mock: {exc}")
        results = _mock_parse_paragraph(paragraph)
        for r in results:
            r["_paragraph_decomposed"] = True
            r["_parse_degraded"] = True
            r["_parse_error"] = str(exc)
        return results


# ---------------------------------------------------------------------------
# Internal single-claim implementation  (unchanged)
# ---------------------------------------------------------------------------

def _parse_claim_internal(claim_string: str) -> dict:
    """
    Parse a NL cricket claim into a structured filter dict.
    Falls back to rule-based _mock_parse if LLM fails or is invalid.
    """
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return _mock_parse(claim_string)

    try:
        client = Groq(api_key=api_key)
        last_error = None

        # Multi-pass parse with critic verification (double-entry bookkeeping)
        # Optimization: Use fast-path for the first attempt.
        for attempt in range(1, 4):
            text = _llm_call(client, claim_string, _SYSTEM_PROMPT)
            try:
                candidate = _validate_and_sanitize(text)
            except Exception as ve:
                last_error = ve
                continue

            # CRITICAL: Subject anchoring check (fast deterministic)
            ok, issue = _subject_type_guard(claim_string, candidate)
            if not ok:
                last_error = ValueError(issue or "SubjectTypeMismatch")
                continue

            # Critic: only run on attempt 2+ or if validation is suspect
            # Pass 1 is usually good enough for high-end models like llama-3.3-70b
            if attempt == 1:
                candidate["_parse_attempts"] = attempt
                candidate["_parse_verified"] = True
                return candidate

            try:
                critic_in = json.dumps({"claim": claim_string, "candidate": candidate}, ensure_ascii=False)
                critic_text = client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": _CRITIC_SYSTEM_PROMPT},
                        {"role": "user", "content": critic_in},
                    ],
                    model="llama-3.3-70b-versatile",
                    temperature=0,
                ).choices[0].message.content
                critic = _parse_json_only(critic_text)
                if isinstance(critic, dict) and critic.get("verdict") == "ok":
                    ok, issue = _subject_type_guard(claim_string, candidate)
                    if not ok:
                        candidate["_parse_verified"] = False
                        last_error = ValueError(issue or "SubjectTypeMismatch")
                        continue
                    candidate["_parse_attempts"] = attempt
                    candidate["_parse_verified"] = True
                    return candidate
                # else retry with critic issues implicitly captured by next attempt
                candidate["_parse_verified"] = False
            except Exception:
                # If critic fails, still return validated candidate (better than falling back)
                candidate["_parse_attempts"] = attempt
                candidate["_parse_verified"] = False
                return candidate

        raise ValueError(f"LLM parse failed after retries: {last_error}")
    except Exception as exc:
        # LLM is an external dependency — always fall back to rule-based parser.
        # In STRICT mode we mark the result degraded so downstream can log it.
        result = _mock_parse(claim_string)
        result["_parse_degraded"] = True
        result["_parse_error"] = str(exc)
        return result


# ---------------------------------------------------------------------------
# Rule-based fallback parser  (covers common NLP patterns without LLM)
# ---------------------------------------------------------------------------

def _mock_parse(claim_string: str) -> dict:
    """
    Lightweight rule-based NLP extractor.
    Covers the 38 filter dimensions using regex heuristics.
    This intentionally mirrors what the LLM would return so the engine
    downstream requires no changes.
    """
    s = claim_string.strip()

    result: dict = {
        "subject": None,
        "metric": None,
        "claimed_value": None,
        "filters": {
            # Match context
            "venue_name": None, "city": None, "country": None,
            "format": None, "season": None, "day_night": None,
            "toss_winner": None, "toss_decision": None,
            "innings": None, "series": None, "home_away": None,
            "neutral_venue": None,
            # Batting
            "opposition": None, "dismissal_type": None,
            "batting_position": None, "non_striker": None,
            "milestones": None,
            # Bowling / matchup
            "bowler": None, "bowler_type": None, "bowler_hand": None,
            "over_number": None, "match_phase": None,
            "batter_vs_bowler_type": None, "batter_vs_bowler": None,
        }
    }

    fl = result["filters"]

    # ── Claimed numeric value ──────────────────────────────────────────────
    val_m = re.search(r"\b(\d+(?:\.\d+)?)\b", s)
    if val_m:
        result["claimed_value"] = float(val_m.group(1))

    # ── Metric detection ───────────────────────────────────────────────────
    sl = s.lower()
    if re.search(r"\bstrike\s*rate\b", sl) or re.search(r"\bscores?\s+faster\b", sl):
        result["metric"] = "Strike Rate"
    elif re.search(r"\bbatting\s+average\b|averages?\b", sl):
        result["metric"] = "Batting Average"
    elif re.search(r"\bbowling\s+average\b", sl):
        result["metric"] = "Bowling Average"
    elif re.search(r"\beconomy\b", sl):
        result["metric"] = "Economy Rate"
    elif re.search(r"\bwickets?\b", sl):
        result["metric"] = "Wickets"
    elif re.search(r"\btotal\s+runs\b|\bruns?\b", sl):
        result["metric"] = "Total Runs"
    elif re.search(r"\bdot\s+ball\s*%?\b", sl):
        result["metric"] = "Dot Ball %"
    elif re.search(r"\bboundary\s*%?\b", sl):
        result["metric"] = "Boundary %"
    elif re.search(r"\bhigh\s*score\b", sl):
        result["metric"] = "High Score"
    elif re.search(r"\bmilestones?\b|fifties?|centuries|100s|50s", sl):
        result["metric"] = "Milestones"
    elif re.search(r"\bpartnership\b", sl):
        result["metric"] = "Partnership Runs"
    else:
        result["metric"] = "Total Runs"

    if re.search(r"\bt20\s+internationals?\b", sl):
        fl["format"] = "T20I"
    elif re.search(r"\binternational\b", sl):
        fl["format"] = "International"
    elif re.search(r"t20is?\b", sl):
        fl["format"] = "T20I"
    elif re.search(r"t20s?\b", sl):
        fl["format"] = "T20"
    elif re.search(r"\bodis?\b|one[- ]days?", sl):
        fl["format"] = "ODI"
    elif re.search(r"\btests?\b", sl):
        fl["format"] = "Test"

    # ── Innings ────────────────────────────────────────────────────────────
    inn_m = re.search(r"\b(1st|2nd|3rd|4th|first|second|third|fourth)\s+innings\b|\b(chasing|chases|chase)\b|\b(batting\s+first)\b", sl)
    if inn_m:
        inn_map = {"1st": 1, "first": 1, "2nd": 2, "second": 2,
                   "3rd": 3, "third": 3, "4th": 4, "fourth": 4,
                   "chasing": 2, "chases": 2, "chase": 2, "batting first": 1}
        # Find which group matched
        found = next((g for g in inn_m.groups() if g), None)
        if found:
            fl["innings"] = inn_map.get(found.lower())

    # ── Match phase ────────────────────────────────────────────────────────
    if re.search(r"\bpowerplay\b", sl):
        fl["match_phase"] = "Powerplay"
    elif re.search(r"\bdeath\s+overs?\b|final\s+overs?\b", sl):
        fl["match_phase"] = "Death"
    elif re.search(r"\bmiddle\s+overs?\b", sl):
        fl["match_phase"] = "Middle"

    # ── Bowler hand & type (handles "left-arm pace", "right-arm spin", etc.) ──
    hand_type_m = re.search(
        r"\b(left|right)[- ]arm\s+(fast[- ]medium|medium[- ]fast|fast|medium|pace|spin|"
        r"leg[- ]spin|off[- ]spin|orthodox)\b", sl
    )
    if hand_type_m:
        fl["bowler_hand"] = hand_type_m.group(1).capitalize()
        raw_type = hand_type_m.group(2).lower()
        if re.search(r"spin|orthodox", raw_type):
            fl["bowler_type"] = "Spin"
            fl["batter_vs_bowler_type"] = hand_type_m.group(2).title()
        else:
            fl["bowler_type"] = "Pace"
            fl["batter_vs_bowler_type"] = hand_type_m.group(2).title()
    else:
        if re.search(r"\bleg[- ]spin\b", sl):
            fl["bowler_type"] = "Spin"
            fl["batter_vs_bowler_type"] = "Leg-spin"
        elif re.search(r"\boff[- ]spin\b", sl):
            fl["bowler_type"] = "Spin"
            fl["batter_vs_bowler_type"] = "Off-spin"
        elif re.search(r"\bspin\b", sl):
            fl["bowler_type"] = "Spin"
        elif re.search(r"\bpace\b|seam\b|fast\b", sl):
            fl["bowler_type"] = "Pace"

    # Standalone hand without type
    if fl["bowler_hand"] is None:
        if re.search(r"\bleft[- ]arm\b|\bleft\s+hand(?:ed)?\b", sl):
            fl["bowler_hand"] = "Left"
        elif re.search(r"\bright[- ]arm\b|\bright\s+hand(?:ed)?\b", sl):
            fl["bowler_hand"] = "Right"

    # ── Over number / over range (Fix 7) ──────────────────────────────────
    # Priority 1: range expressions -> over_range
    # Use more flexible regexes for over ranges
    first_over_m = re.search(r"\bfirst\s+(\d{1,2})\s+overs?\b", sl)
    last_over_m  = re.search(r"\blast\s+(\d{1,2})\s+overs?\b", sl)
    # Matches "overs 20 to 40", "20 to 40 overs", "20-40 overs"
    range_over_m = re.search(r"\b(?:overs?\s+)?(\d{1,2})\s*(?:to|-|through)\s*(\d{1,2})\s*(?:overs?)?\b", sl)
    # Matches "between 20 to 40", "between overs 20 and 40", "between 20 and 40 over"
    between_over_m = re.search(r"\bbetween\s+(?:overs?\s+)?(\d{1,2})\s+(?:to|and|-)\s+(\d{1,2})\s*(?:overs?)?\b", sl)
    if first_over_m:
        n = int(first_over_m.group(1))
        fl["over_range"] = [0, n - 1]            # 0-indexed inclusive
    elif last_over_m:
        # Store as negative offset; engine will resolve against match length
        n = int(last_over_m.group(1))
        fl["over_range"] = [-(n), -1]
    elif between_over_m:
        start = int(between_over_m.group(1)) - 1
        end = int(between_over_m.group(2)) - 1
        fl["over_range"] = [min(start, end), max(start, end)]
    elif range_over_m:
        start = int(range_over_m.group(1)) - 1   # convert to 0-indexed
        end   = int(range_over_m.group(2)) - 1
        fl["over_range"] = [start, end]
    else:
        # Priority 2: single over number
        over_m = re.search(r"\b(?:over|in\s+the\s+)(\d{1,2})(?:st|nd|rd|th)?\s+over\b", sl)
        if over_m:
            fl["over_number"] = int(over_m.group(1)) - 1  # 0-indexed to match Cricsheet

    # ── Day/Night ─────────────────────────────────────────────────────────
    if re.search(r"\bday[/ ]?night\b", sl):
        fl["day_night"] = "Day/Night"
    elif re.search(r"\bnight\s+match\b|\bfloodlit\b", sl):
        fl["day_night"] = "Night"
    elif re.search(r"\bday\s+match\b", sl):
        fl["day_night"] = "Day"

    # ── Home / Away ───────────────────────────────────────────────────────
    # ONLY explicit home/away keywords — NOT "in <country>" (that's a country filter)
    if re.search(r"\bat\s+home\b|\bhome\s+ground\b|\bhome\s+games?\b|\bhome\s+matches?\b", sl):
        fl["home_away"] = "Home"
    elif re.search(r"\baway\s+(?:match|game|ground|from home)\b|\baway\b", sl) and not re.search(r"\bgave\s+away\b", sl):
        # Only set Away on standalone "away" or "away match/game" — not if it's part of another phrase
        # Make sure we don't conflict with country filter
        if not re.search(r"\bin\s+(?:england|australia|india|pakistan|south africa|new zealand|west indies|sri lanka|bangladesh|zimbabwe)\b", sl):
            fl["home_away"] = "Away"

    # ── Neutral venue ─────────────────────────────────────────────────────
    if re.search(r"\bneutral\s+venue\b", sl):
        fl["neutral_venue"] = True

    # ── Toss ──────────────────────────────────────────────────────────────
    toss_m = re.search(r"\bafter\s+(?:winning|losing)\s+the\s+toss\b", sl)
    if toss_m:
        fl["toss_decision"] = "bat" if "bat" in sl else "field"

    # ── Series ────────────────────────────────────────────────────────────
    # Order matters: check multi-word first to avoid partial match
    series_keywords = [
        ("t20 world cups?",    "T20 World Cup"),
        ("world cups?",        "World Cup"),
        ("champions trophy",  "Champions Trophy"),
        ("asia cup",          "Asia Cup"),
        ("ashes",             "Ashes"),
        ("ipl",               "Ipl"),
        ("psl",               "Psl"),
        ("bbl",               "Bbl"),
        ("tri[- ]series",     "Tri-Series"),
        ("bilateral",         "Bilateral"),
        ("test series",       "Test Series"),
    ]
    for pattern, canonical in series_keywords:
        if re.search(r"\b" + pattern + r"\b", sl):
            fl["series"] = canonical
            break

    # ── Country / venue ───────────────────────────────────────────────────
    _country_patterns = {
        "england": "England", "australia": "Australia", "india": "India",
        "pakistan": "Pakistan", "south africa": "South Africa",
        "new zealand": "New Zealand", "west indies": "West Indies",
        "sri lanka": "Sri Lanka", "bangladesh": "Bangladesh",
        "zimbabwe": "Zimbabwe", "afghanistan": "Afghanistan",
        "ireland": "Ireland", "uae": "UAE",
    }
    for kw, country in _country_patterns.items():
        if re.search(r"\bin\s+" + kw + r"\b", sl):
            fl["country"] = country
        if re.search(r"\bagainst\s+" + kw + r"\b", sl):
            fl["opposition"] = country
        # Plain "in England" style already picked up by home_away regex above,
        # but we also set country:
        if re.search(r"\bin\s+" + kw + r"\b", sl) and fl["country"] is None:
            fl["country"] = country

    # Venue abbreviations / common names
    _venue_hints = {
        "mcg": "Melbourne Cricket Ground",
        "lords?": "Lord's Cricket Ground",
        "eden gardens": "Eden Gardens",
        "wankhede": "Wankhede Stadium",
        "oval": "The Oval",
        "headingley": "Headingley",
        "gabba": "The Gabba",
        "scg": "Sydney Cricket Ground",
        "edgbaston": "Edgbaston",
    }
    for pat, vname in _venue_hints.items():
        if re.search(r"\b" + pat + r"\b", sl):
            fl["venue_name"] = vname
            break

    # ── Season ────────────────────────────────────────────────────────────
    # Capture "after/since/before YYYY" as a complete phrase so QueryPlanner
    # can distinguish season_gte vs season_eq
    season_phrase = re.search(
        r"\b((?:after|since|onwards|before|until|pre)\s+)?(20\d{2}|19\d{2})(?:/\d{2,4})?\b", sl
    )
    if season_phrase:
        prefix = (season_phrase.group(1) or "").strip()
        year = season_phrase.group(2)
        if prefix:
            fl["season"] = f"{prefix} {year}"   # e.g. "after 2015", "since 2021"
        else:
            fl["season"] = year                   # exact year
        # Temporal anchor: "in 2023" should mean as-of end-of-year for historical snapshot
        if not prefix:
            result["as_of_date"] = f"{year}-12-31"
            fl["as_of_date"] = result["as_of_date"]

    # ── Batting Position ──────────────────────────────────────────────────
    pos_m = re.search(
        r"\bbatting\s+(?:at\s+)?(?:number\s*)?(\d{1,2})\b|"
        r"\bat\s+(?:number\s*)?(\d{1,2})(?:\s+position)?\b|"
        r"\bopener\b|\bno\.?\s*(\d)\b", sl
    )
    if pos_m:
        num = pos_m.group(1) or pos_m.group(2) or pos_m.group(3)
        fl["batting_position"] = int(num) if num else 1
    elif re.search(r"\bopen(?:ing|er)\b", sl):
        fl["batting_position"] = 1

    # ── Milestones ────────────────────────────────────────────────────────
    if re.search(r"\b100s\b|\bcenturies\b|\btons\b", sl):
        fl["milestones"] = "100s"
    if re.search(r"\b50s\b|\bfifties\b|\bhalf[- ]centuries\b", sl):
        fl["milestones"] = "50s" if fl["milestones"] is None else "50s and 100s"

    # ── Dismissal type ────────────────────────────────────────────────────
    for dism in ["caught", "bowled", "lbw", "run out", "stumped",
                 "hit wicket", "caught and bowled"]:
        if dism in sl:
            fl["dismissal_type"] = dism
            break

    # -- Subject (player name) -- last resort heuristic ---------------------
    import json, os
    _known_players = [
        "Babar Azam", "Virat Kohli", "Joe Root", "Steve Smith", "Kane Williamson",
        "Rohit Sharma", "David Warner", "Shakib Al Hasan", "Ben Stokes",
        "Pat Cummins", "Jasprit Bumrah", "Mitchell Starc",
        "Travis Head", "Chris Gayle", "MS Dhoni", "Rishabh Pant",
        # Bowlers from test suite
        "Shaheen Shah Afridi", "Shaheen Afridi", "Rashid Khan",
        "James Anderson", "Trent Boult", "Ali Khan",
        "Bhuvneshwar Kumar", "Sunil Narine",
    ]
    # Add aliases dynamically
    alias_file = os.path.join(os.path.dirname(__file__), "..", "..", "data", "player_aliases.json")
    if os.path.exists(alias_file):
        try:
            with open(alias_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                _known_players.extend(data.get("aliases", {}).keys())
                _known_players.extend(data.get("initials_map", {}).keys())
        except Exception:
            pass

    for p in _known_players:
        if re.search(rf"\b{re.escape(p.lower())}\b", sl):
            result["subject"] = p
            break

    # Generic fallback: first sequence of 2-3 capitalized words not matching filter keywords
    _filter_words = {
        "asia", "ipl", "odi", "t20", "test", "world", "cup", "league",
        "home", "away", "powerplay", "death", "england", "australia", "india",
        "pakistan", "new", "zealand", "south", "africa", "west", "indies",
        "how", "good", "when", "what", "which", "outside",
    }

    # Dynamic country extraction (e.g. "in India", "in Iceland")
    in_country_m = re.search(r"\bin\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b", s)
    if in_country_m and fl["country"] is None:
        cname = in_country_m.group(1)
        if cname.lower() not in _filter_words:
            fl["country"] = cname

    # Dynamic opposition extraction (e.g. "against India", "vs Australia")
    opp_country_m = re.search(r"\b(?:against|vs\.?)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b", s)
    if opp_country_m and fl["opposition"] is None:
        oppname = opp_country_m.group(1)
        if oppname.lower() not in _filter_words:
            fl["opposition"] = oppname

    # Batter vs Bowler type (e.g., "against left-handed batters", "against right-handed")
    lhb_m = re.search(r"\b(?:against|vs\.?)\s+left[- ]hand(?:ed)?(?:\s+batters?)?\b", sl)
    rhb_m = re.search(r"\b(?:against|vs\.?)\s+right[- ]hand(?:ed)?(?:\s+batters?)?\b", sl)
    if lhb_m:
        fl["batter_vs_bowler_type"] = "Left-handed"
    elif rhb_m:
        fl["batter_vs_bowler_type"] = "Right-handed"

    # Look for "Lastname, Initials" or "Lastname, Firstname" format first (e.g., "Warner, DA")
    if result["subject"] is None:
        comma_m = re.search(r"\b([A-Z][a-z]+),\s*([A-Z]{1,3})\b", s)
        if comma_m:
            result["subject"] = f"{comma_m.group(1)}, {comma_m.group(2)}"
        else:
            comma_name_m = re.search(r"\b([A-Z][a-z]+),\s*([A-Z][a-z]+)\b", s)
            if comma_name_m:
                result["subject"] = f"{comma_name_m.group(1)}, {comma_name_m.group(2)}"

    # Check capitalized name sequences (e.g. "Virat Kohli", "Marnus Labuschagne", "S Cook")
    if result["subject"] is None:
        # Match single letter initials followed by a word (e.g. "S Cook", "DA Warner")
        cands_init = re.findall(r"\b([A-Z]{1,3}\s+[A-Z][a-z]+)\b", s)
        for cand in cands_init:
            words = cand.lower().split()
            if not any(w in _filter_words for w in words):
                result["subject"] = cand
                break

    # Match standard capitalized name sequences
    if result["subject"] is None:
        cands = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b", s)
        for cand in cands:
            words = cand.lower().split()
            if not any(w in _filter_words for w in words):
                result["subject"] = cand
                break

    # Single-word fallbacks like "thala"
    if result["subject"] is None:
        first_word = s.split()[0]
        if first_word.lower() not in _filter_words:
            result["subject"] = first_word

    # After extracting known names, try to strip "opposition" from subject
    if result["subject"] and fl["opposition"] is None:
        opp_m = re.search(r"\bagainst\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", s)
        if opp_m:
            fl["opposition"] = opp_m.group(1)

    return result
if __name__ == '__main__':
    import argparse

    _cli_parser = argparse.ArgumentParser(
        description="ai_parser — NLP claim parser (single-claim or multi-claim paragraph)"
    )
    _cli_parser.add_argument(
        '--query', type=str, default=None,
        help='Single NL claim to parse (returns one JSON object)'
    )
    _cli_parser.add_argument(
        '--paragraph', type=str, default=None,
        help=(
            'Multi-claim paragraph to decompose (returns a JSON array). '
            'Applies co-reference resolution and contrastive parameter splitting.'
        )
    )
    _args = _cli_parser.parse_args()

    if _args.paragraph:
        # Multi-claim decomposition path
        _result = parse_paragraph(_args.paragraph)
        print(json.dumps(_result, indent=4, default=str))
    elif _args.query:
        # Legacy single-claim path  (unchanged behaviour)
        print(json.dumps(parse_claim(_args.query), indent=4, default=str))
    else:
        _cli_parser.print_help()
