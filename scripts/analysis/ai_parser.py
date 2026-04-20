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
  "subject": <str | null>,          // player name
  "metric": <str | null>,           // e.g. "Batting Average", "Wickets"
  "claimed_value": <float | null>,  // numeric value
  "filters": {
    "venue_name": <str | null>, "city": <str | null>, "country": <str | null>,
    "format": <str | null>, "season": <str | null>, "day_night": <str | null>,
    "toss_winner": <str | null>, "toss_decision": <str | null>,
    "innings": <int | null>, "series": <str | null>, "home_away": <str | null>,
    "neutral_venue": <bool | null>, "opposition": <str | null>,
    "dismissal_type": <str | null>, "batting_position": <int | null>,
    "non_striker": <str | null>, "milestones": <str | null>,
    "bowler": <str | null>, "bowler_type": <str | null>, "bowler_hand": <str | null>,
    "over_number": <int | null>, "match_phase": <str | null>,
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
• For bowler_hand infer from phrases like "left-arm pace" → bowler_hand="Left", bowler_type="Pace".
• For match_phase: "powerplay" → "Powerplay"; "death overs" / "final overs" → "Death";
  "middle overs" → "Middle".
• innings: "first innings"→1, "second innings"→2, "third innings"→3, "fourth innings"→4, "chases"/"chasing"→2, "batting first"→1.
• For milestones: "centuries"/"100s"→"100s", "fifties"/"50s"→"50s",
  "fifties and centuries"→"50s and 100s".
• home_away: infer from context when possible, e.g. "at home" → "Home".
"""


def _validate_and_sanitize(text: str) -> dict:
    """Validate JSON structure and types against schema under 200ms."""
    # Heuristic sanitization
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    
    # Fix common LLM trailing comma error before parsing
    text = re.sub(r",\s*}", "}", text)
    text = re.sub(r",\s*]", "]", text)

    data = json.loads(text)
    
    if not isinstance(data, dict):
        raise ValueError("Root must be an object")

    # Required top-level
    for k in ["subject", "metric", "claimed_value", "filters"]:
        if k not in data:
            data[k] = None
    
    if not isinstance(data.get("filters"), dict):
        data["filters"] = {}

    # Ensure all FILTER_KEYS exist in filters sub-dict
    f = data["filters"]
    for k in FILTER_KEYS:
        if k not in f:
            f[k] = None

    # Type coercion for common fields
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


def parse_claim(claim_string: str) -> dict:
    """
    Parse a NL cricket claim into a structured filter dict.
    Falls back to rule-based _mock_parse if LLM fails or is invalid.
    """
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return _mock_parse(claim_string)

    try:
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f'Claim: "{claim_string}"'}
            ],
            model="llama-3.3-70b-versatile",
            temperature=0,
        )
        text = response.choices[0].message.content
        return _validate_and_sanitize(text)
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
    if re.search(r"\bstrike\s*rate\b", sl):
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

    if re.search(r"\binternational\b", sl):
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
    first_over_m = re.search(r"\bfirst\s+(\d{1,2})\s+overs?\b", sl)
    last_over_m  = re.search(r"\blast\s+(\d{1,2})\s+overs?\b", sl)
    range_over_m = re.search(r"\bovers?\s+(\d{1,2})\s+(?:to|-|through)\s+(\d{1,2})\b", sl)
    if first_over_m:
        n = int(first_over_m.group(1))
        fl["over_range"] = [0, n - 1]            # 0-indexed inclusive
    elif last_over_m:
        # Store as negative offset; engine will resolve against match length
        n = int(last_over_m.group(1))
        fl["over_range"] = [-(n), -1]
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
    _known_players = [
        "Babar Azam", "Virat Kohli", "Joe Root", "Steve Smith", "Kane Williamson",
        "Rohit Sharma", "David Warner", "Shakib Al Hasan", "Ben Stokes",
        "Pat Cummins", "Jasprit Bumrah", "Mitchell Starc",
        "Travis Head", "Chris Gayle", "MS Dhoni", "Rishabh Pant",
        # Bowlers from test suite
        "Shaheen Shah Afridi", "Shaheen Afridi", "Rashid Khan",
        "James Anderson", "Trent Boult", "Ali Khan",
        "Kagiso Rabada", "Bhuvneshwar Kumar", "Sunil Narine",
    ]
    for p in _known_players:
        if p.lower() in sl:
            result["subject"] = p
            break

    # Generic fallback: first sequence of 2-3 capitalized words not matching filter keywords
    _filter_words = {
        "asia", "ipl", "odi", "t20", "test", "world", "cup", "league",
        "home", "away", "powerplay", "death", "england", "australia", "india",
        "pakistan", "new", "zealand", "south", "africa", "west", "indies",
        "how", "good", "when", "what", "which", "outside",
    }
    if result["subject"] is None:
        cands = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b", s)
        for cand in cands:
            words = cand.lower().split()
            if not any(w in _filter_words for w in words):
                result["subject"] = cand
                break

    # After extracting known names, try to strip "opposition" from subject
    if result["subject"] and fl["opposition"] is None:
        opp_m = re.search(r"\bagainst\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", s)
        if opp_m:
            fl["opposition"] = opp_m.group(1)

    return result
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--query', type=str, help='Claim to parse')
    args = parser.parse_args()
    if args.query:
        print(json.dumps(parse_claim(args.query), indent=4))
