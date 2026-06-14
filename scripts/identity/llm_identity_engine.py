"""
llm_identity_engine.py — Neural-Symbolic Hybrid Identity Engine
================================================================
Upgrades fuzzy RapidFuzz resolution with:

  1. LLM-Alias Expansion    — "Thala" → "MS Dhoni" via player_aliases.json +
                               Groq LLM fallback for unknown slang.
  2. Player Fingerprint     — Static authority score (match count, recency,
                               role affinity) applied to every candidate.
  3. Cross-Encoder Re-Ranker— Top-20 candidates re-ranked by Groq LLM with
                               full query context (metric, phase, role).
  4. Role-Based Filtering   — Infers BATTER / BOWLER entity type from the
                               query metric before candidate generation.
  5. N-Gram Embedding Search— (Stub, ready for Qdrant/sentence-transformers)
                               Falls back to RapidFuzz for offline mode.

Public API (drop-in alongside identity_engine.py):
    engine = LLMIdentityEngine()
    result = engine.resolve(query, context={...})
    result = engine.resolve_for_ingestion(raw_name, team)
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from rapidfuzz import fuzz, process as rf_process

load_dotenv()
log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)

# ─── Paths ────────────────────────────────────────────────────────────────────
ROOT            = Path(__file__).resolve().parents[2]
PLAYERS_DB_FILE = ROOT / "Dataset" / "Players" / "players_data_with_all_info.csv"
ALIASES_FILE    = ROOT / "data" / "player_aliases.json"

# ─── Tuning ───────────────────────────────────────────────────────────────────
FUZZY_CUTOFF       = 78     # Stage-A candidate gate
TOP_K_CANDIDATES   = 20     # Cross-encoder re-ranker input
LLM_RERANK_THRESH  = 3      # Only call LLM if > this many Stage-A candidates
LLM_MODEL          = "llama-3.3-70b-versatile"
LLM_TEMPERATURE    = 0

# ─── Metric → role inference ─────────────────────────────────────────────────
_BATTER_METRICS  = {
    "batting average", "strike rate", "total runs", "high score", "dot ball %",
    "boundary %", "milestones", "partnership runs", "balls faced",
}
_BOWLER_METRICS  = {
    "economy rate", "wickets", "bowling average", "bowling strike rate",
    "dots forced", "extras conceded", "runs conceded in over",
}


def infer_entity_type(metric: str | None) -> str | None:
    """Return 'batter', 'bowler', or None based on metric string."""
    if not metric:
        return None
    m = metric.strip().lower()
    if m in _BATTER_METRICS:
        return "batter"
    if m in _BOWLER_METRICS:
        return "bowler"
    return None


# ─── Authority Score ──────────────────────────────────────────────────────────

def _compute_authority_score(
    row: pd.Series,
    match_count_map: dict[str, int],
    as_of_year: int | None,
    prefer_role: str | None,
    prefer_bowling_type: str | None,
) -> float:
    """
    Feature 2: Player Fingerprint — static authority score.
      +0.5 max  — log-normalised match count (popularity)
      +0.2      — temporal relevance (active if as_of_year >= 2018)
      +0.2      — role affinity (metric implies bowling → bowler boost)
    """
    pid = str(row.get("id", ""))
    score = 0.0

    # Popularity: log(match_count + 1) normalised to [0, 0.5]
    cnt = match_count_map.get(pid, 0)
    if cnt > 0:
        score += min(math.log1p(cnt) / math.log1p(500), 0.5)

    # Temporal relevance
    dob_str = str(row.get("dateofbirth", ""))
    if as_of_year:
        try:
            dob_year = int(dob_str.split("-")[-1]) if "-" in dob_str else int(dob_str[-4:])
            age_at_query = as_of_year - dob_year
            # Players aged 18-38 are likely active; outside = retire penalty
            if 18 <= age_at_query <= 38:
                score += 0.2
            elif age_at_query < 18 or age_at_query > 45:
                score -= 0.1
        except Exception:
            pass

    # Role affinity
    position = str(row.get("position", "")).lower()
    bowl_style = str(row.get("bowlingstyle", "")).lower()

    if prefer_role == "bowler":
        if "bowler" in position:
            score += 0.2
        elif "batsman" in position or "wicketkeeper" in position:
            score -= 0.05
        if prefer_bowling_type:
            if prefer_bowling_type in bowl_style:
                score += 0.1
    elif prefer_role == "batter":
        if "batsman" in position or "wicketkeeper" in position:
            score += 0.2
        elif position in ("bowler",):
            score -= 0.05

    return round(max(0.0, score), 4)


# ─── Alias Expander ───────────────────────────────────────────────────────────

class AliasExpander:
    """
    Feature 3: LLM-Driven Alias Expansion.
    Resolves nicknames / initials / slang → canonical player name.
    Chain: JSON sidecar → Groq LLM fallback.
    """

    def __init__(self):
        self._static: dict[str, str] = {}
        self._initials: dict[str, str] = {}
        self._llm_cache: dict[str, str | None] = {}
        self._load_aliases()
        self._groq = None
        api_key = os.environ.get("GROQ_API_KEY")
        if api_key:
            try:
                from groq import Groq
                self._groq = Groq(api_key=api_key)
            except Exception:
                log.warning("[AliasExpander] Groq unavailable — LLM alias fallback disabled.")

    def _load_aliases(self):
        if not ALIASES_FILE.exists():
            log.warning("[AliasExpander] aliases file not found: %s", ALIASES_FILE)
            return
        with open(ALIASES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._static   = {k.lower(): v for k, v in data.get("aliases", {}).items()}
        self._initials = {k.lower(): v for k, v in data.get("initials_map", {}).items()}
        log.info("[AliasExpander] Loaded %d aliases, %d initials.", len(self._static), len(self._initials))

    def expand(self, raw: str) -> str:
        """Return canonical name or original string if no alias found."""
        key = raw.strip().lower()
        if key in self._static:
            log.debug("[AliasExpander] static hit: '%s' → '%s'", raw, self._static[key])
            return self._static[key]
        if key in self._initials:
            return self._initials[key]
        # LLM fallback for unknown slang
        expanded = self._llm_expand(raw)
        return expanded if expanded else raw

    def _llm_expand(self, raw: str) -> str | None:
        if not self._groq:
            return None
        if raw.lower() in self._llm_cache:
            return self._llm_cache[raw.lower()]
        prompt = (
            "You are a cricket expert. Given the nickname, slang, or initials below, "
            "return ONLY the canonical full player name (e.g. 'MS Dhoni'). "
            "If you are not sure, return the string UNKNOWN.\n\n"
            f"Input: \"{raw}\"\nOutput:"
        )
        try:
            resp = self._groq.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=LLM_MODEL,
                temperature=LLM_TEMPERATURE,
                max_tokens=30,
            )
            answer = resp.choices[0].message.content.strip().strip('"').strip("'")
            result = None if answer.upper() == "UNKNOWN" else answer
            self._llm_cache[raw.lower()] = result
            log.debug("[AliasExpander] LLM: '%s' → '%s'", raw, result)
            return result
        except Exception as e:
            log.debug("[AliasExpander] LLM error: %s", e)
            return None

# ─── Main Engine ──────────────────────────────────────────────────────────────

class LLMIdentityEngine:
    """
    Neural-Symbolic Hybrid Identity Engine.

    Resolution pipeline:
      Step 0  — Alias expansion  (nicknames/slang → canonical)
      Step 1  — Exact variant lookup (O(1))
      Step 2  — Role-based filtering (BATTER / BOWLER entity type)
      Step 3  — Stage-A: RapidFuzz top-K candidate generation
      Step 4  — Player Fingerprint scoring (authority score)
      Step 5  — Stage-B: Groq LLM cross-encoder re-ranking
      Step 6  — Return best candidate with confidence
    """

    def __init__(self, match_count_map: dict[str, int] | None = None):
        """
        Args:
            match_count_map: {player_id: match_count} for authority scoring.
                             If None, popularity scoring is skipped (no boost).
        """
        self.db: pd.DataFrame = pd.DataFrame()
        self.lookup_map: dict[str, list[str]] = defaultdict(list)   # variant_lower → [pid]
        self.full_names: list[str] = []
        self._metadata_rows: dict[str, pd.Series] = {}
        self._fuzzy_cache: dict[str, list[str]] = {}
        self.lookup_by_len: dict[int, list[str]] = defaultdict(list)
        self.match_count_map: dict[str, int] = match_count_map or {}

        self.alias_expander = AliasExpander()
        self._groq = None
        api_key = os.environ.get("GROQ_API_KEY")
        if api_key:
            try:
                from groq import Groq
                self._groq = Groq(api_key=api_key)
                log.info("[LLMIdentityEngine] Groq LLM re-ranker ready.")
            except Exception:
                log.warning("[LLMIdentityEngine] Groq unavailable — cross-encoder disabled.")

        self._load_db()

    # ── DB Loading ────────────────────────────────────────────────────────────
    def _load_db(self):
        if not PLAYERS_DB_FILE.exists():
            log.error("[LLMIdentityEngine] Players DB not found: %s", PLAYERS_DB_FILE)
            return
        self.db = pd.read_csv(PLAYERS_DB_FILE, dtype={"id": str})
        self._build_lookup()
        log.info("[LLMIdentityEngine] Loaded %d players.", len(self.db))

    def _build_lookup(self):
        for _, row in self.db.iterrows():
            pid   = str(row["id"])
            fname = str(row.get("fullname",  "")).strip()
            first = str(row.get("firstname", "")).strip()
            last  = str(row.get("lastname",  "")).strip()
            if not fname or fname == "nan":
                continue

            variants: set[str] = set()
            variants.add(fname.lower())
            variants.add(fname.replace(" ", "").lower())
            if first and first != "nan" and last and last != "nan":
                variants.add(f"{first[0]} {last}".lower())
                if len(first) >= 2:
                    variants.add(f"{first[:2]} {last}".lower())
                variants.add(f"{first} {last}".lower())
            if last and last != "nan":
                variants.add(last.lower())
            if first and first != "nan":
                variants.add(first.lower())

            for v in variants:
                if pid not in self.lookup_map[v]:
                    self.lookup_map[v].append(pid)
                wc = len(v.split())
                if v not in self.lookup_by_len[wc]:
                    self.lookup_by_len[wc].append(v)

            self._metadata_rows[pid] = row

        self.full_names = self.db["fullname"].dropna().unique().tolist()

    # ── Metadata ──────────────────────────────────────────────────────────────
    @lru_cache(maxsize=8192)
    def _get_metadata(self, pid: str) -> dict:
        row = self._metadata_rows.get(pid)
        if row is None:
            return {}
        bowl_style = str(row.get("bowlingstyle", "")).lower()
        bat_style  = str(row.get("battingstyle",  "")).lower()

        if any(k in bowl_style for k in ["spin","break","orthodox","chinaman","googly"]):
            bowl_type = "Spin"
        elif any(k in bowl_style for k in ["fast","medium","seam","pace"]):
            bowl_type = "Pace"
        else:
            bowl_type = "Unknown"

        batter_hand = "Left" if "left" in bat_style else ("Right" if "right" in bat_style else "Unknown")
        bowler_hand = "Left" if "left" in bowl_style else ("Right" if "right" in bowl_style else "Unknown")

        return {
            "player_id":      pid,
            "canonical_name": str(row.get("fullname", "")).strip(),
            "country":        str(row.get("country_name", "")).strip(),
            "primary_role":   str(row.get("position", "Unknown")).strip(),
            "batting_style":  str(row.get("battingstyle","")).strip() or "Unknown",
            "bowling_style":  str(row.get("bowlingstyle","")).strip() or "Unknown",
            "bowling_type":   bowl_type,
            "batter_hand":    batter_hand,
            "bowler_hand":    bowler_hand,
        }

    # ── Stage-A: Candidate Generation ─────────────────────────────────────────
    def _lookup_segment(self, segment: str) -> list[str]:
        """Exact + RapidFuzz lookup → list of pids."""
        clean = segment.strip().lower()
        if clean in self.lookup_map:
            return self.lookup_map[clean]
        if clean in self._fuzzy_cache:
            return self._fuzzy_cache[clean]

        wc = len(clean.split())
        pool = self.lookup_by_len.get(wc, [])
        if not pool:
            self._fuzzy_cache[clean] = []
            return []

        hits = rf_process.extract(
            clean, pool,
            scorer=fuzz.token_set_ratio,
            score_cutoff=FUZZY_CUTOFF,
            limit=TOP_K_CANDIDATES,
        )
        pids: list[str] = []
        seen: set[str] = set()
        for variant, _score, _ in hits:
            for pid in self.lookup_map.get(variant, []):
                if pid not in seen:
                    pids.append(pid)
                    seen.add(pid)

        self._fuzzy_cache[clean] = pids
        return pids

    def _stage_a_candidates(self, name_clean: str) -> list[str]:
        """Sliding-window token search → top-K unique pid list."""
        tokens = re.split(r"\W+", name_clean.strip().lower())
        n = len(tokens)
        seen: set[str] = set()
        result: list[str] = []
        for window in range(min(4, n), 0, -1):
            for i in range(n - window + 1):
                seg = " ".join(tokens[i: i + window])
                for pid in self._lookup_segment(seg):
                    if pid not in seen:
                        result.append(pid)
                        seen.add(pid)
            if result:
                break
        return result[:TOP_K_CANDIDATES]

    # ── Feature 4: Role Filtering ─────────────────────────────────────────────
    def _apply_role_filter(self, pids: list[str], entity_type: str | None) -> list[str]:
        """
        Feature 4: Multi-Token Role-Based Disambiguation.
        Filters candidates by inferred entity type (batter/bowler).
        Falls back to full list if filter removes all candidates.
        """
        if not entity_type or not pids:
            return pids

        filtered = []
        for pid in pids:
            meta = self._get_metadata(pid)
            role = str(meta.get("primary_role", "")).lower()
            if entity_type == "batter":
                if any(r in role for r in ["batsman", "batting", "allrounder", "wicketkeeper"]):
                    filtered.append(pid)
            elif entity_type == "bowler":
                if any(r in role for r in ["bowler", "allrounder", "bowling"]):
                    filtered.append(pid)

        return filtered if filtered else pids   # never starve the pipeline

    # ── Feature 2: Fingerprint Scoring ────────────────────────────────────────
    def _score_candidates(
        self,
        pids: list[str],
        query_name: str,
        prefer_role: str | None,
        prefer_bowling_type: str | None,
        as_of_year: int | None,
        prefer_country: str | None,
    ) -> list[dict]:
        """Score candidates with fuzzy + authority + contextual weights."""
        scored = []
        for pid in pids:
            meta = self._get_metadata(pid)
            row  = self._metadata_rows.get(pid)
            if not meta or row is None:
                continue

            # Base fuzzy score (0-1)
            base = fuzz.token_set_ratio(
                query_name.lower(), meta["canonical_name"].lower()
            ) / 100.0

            # Authority score (Feature 2)
            authority = _compute_authority_score(
                row, self.match_count_map, as_of_year,
                prefer_role, prefer_bowling_type,
            )

            # Country preference
            country_boost = 0.0
            if prefer_country:
                c = meta.get("country", "").lower()
                if c and c == prefer_country.lower():
                    country_boost = 0.05

            total = round(min(1.0, base + authority + country_boost), 4)
            scored.append({"pid": pid, "score": total, "meta": meta})

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored

    # ── Feature 1: Stage-B LLM Re-Ranker ─────────────────────────────────────
    def _llm_rerank(
        self, query: str, metric: str | None,
        match_phase: str | None, candidates: list[dict],
    ) -> str | None:
        """
        Feature 1: Cross-Encoder LLM Re-Ranker.
        Passes top-K candidates to Groq with full query context.
        Returns the player_id the LLM picks, or None on failure.
        """
        if not self._groq or not candidates:
            return None

        options_text = "\n".join(
            f"  {i+1}. ID={c['pid']}: {c['meta']['canonical_name']} "
            f"({c['meta']['primary_role']}, {c['meta']['country']})"
            for i, c in enumerate(candidates[:TOP_K_CANDIDATES])
        )
        context_parts = [f'Query: "{query}"']
        if metric:
            context_parts.append(f"Metric: {metric}")
        if match_phase:
            context_parts.append(f"Phase: {match_phase}")
        context_str = " | ".join(context_parts)

        prompt = (
            f"You are an elite cricket analyst doing entity resolution.\n"
            f"{context_str}\n\n"
            f"Which of the following player IDs is MOST likely intended?\n"
            f"{options_text}\n\n"
            f"Return ONLY the numeric player ID (e.g. 231). No explanation."
        )
        try:
            resp = self._groq.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=LLM_MODEL,
                temperature=LLM_TEMPERATURE,
                max_tokens=15,
            )
            answer = resp.choices[0].message.content.strip()
            pid_match = re.search(r"\d+", answer)
            if pid_match:
                chosen_pid = pid_match.group()
                # Validate it's actually in our candidates
                if any(c["pid"] == chosen_pid for c in candidates):
                    log.info("[LLMIdentityEngine] LLM re-ranked → pid=%s", chosen_pid)
                    return chosen_pid
        except Exception as e:
            log.debug("[LLMIdentityEngine] LLM re-rank error: %s", e)
        return None

    # ── Public: resolve_for_query ─────────────────────────────────────────────
    def resolve(
        self,
        raw_name: str,
        context: dict | None = None,
    ) -> dict:
        """
        Full resolution pipeline. context keys:
          metric           — e.g. "Economy Rate" (drives role inference)
          match_phase      — e.g. "Death" (sent to LLM re-ranker for context)
          prefer_country   — ISO country string for tie-breaking
          prefer_bowling_type — "Spin"|"Pace"
          as_of_date       — "YYYY-MM-DD" for temporal authority scoring
        """
        if not raw_name or not raw_name.strip():
            return {"resolved": None, "candidates": [], "method": "empty_input"}

        ctx = context or {}
        metric            = ctx.get("metric")
        match_phase       = ctx.get("match_phase")
        prefer_country    = ctx.get("prefer_country", "")
        prefer_bowling_type = (ctx.get("prefer_bowling_type") or "").lower()
        as_of_date_str    = ctx.get("as_of_date", "")
        as_of_year: int | None = None
        if as_of_date_str:
            try:
                as_of_year = int(str(as_of_date_str)[:4])
            except Exception:
                pass

        # Step 0: Alias expansion
        expanded = self.alias_expander.expand(raw_name)
        if expanded != raw_name:
            log.info("[LLMIdentityEngine] Alias: '%s' → '%s'", raw_name, expanded)

        # Step 1: Entity type inference (Feature 4)
        entity_type = infer_entity_type(metric)
        prefer_role = entity_type   # "batter" | "bowler" | None

        # Step 2: Stage-A candidate generation
        candidates_pids = self._stage_a_candidates(expanded)
        if not candidates_pids:
            # Try original if alias was wrong
            if expanded != raw_name:
                candidates_pids = self._stage_a_candidates(raw_name)
        if not candidates_pids:
            return {"resolved": None, "candidates": [], "method": "no_match",
                    "alias_expanded": expanded if expanded != raw_name else None}

        # Step 3: Role filter (Feature 4)
        candidates_pids = self._apply_role_filter(candidates_pids, entity_type)

        # Step 4: Fingerprint scoring (Feature 2)
        scored = self._score_candidates(
            candidates_pids, expanded,
            prefer_role, prefer_bowling_type,
            as_of_year, prefer_country,
        )
        if not scored:
            return {"resolved": None, "candidates": [], "method": "scoring_failed"}

        method = "fingerprint_scored"

        # Step 5: LLM re-ranker (Feature 1) — only if ambiguous
        best_pid = scored[0]["pid"]
        if len(scored) > LLM_RERANK_THRESH and self._groq:
            llm_pid = self._llm_rerank(
                raw_name, metric, match_phase, scored
            )
            if llm_pid:
                best_pid = llm_pid
                method = "llm_cross_encoder"

        # Build result
        best_meta  = self._get_metadata(best_pid)
        best_score = next((s["score"] for s in scored if s["pid"] == best_pid), scored[0]["score"])

        return {
            "resolved": {
                **best_meta,
                "confidence": best_score,
                "alias_expanded": expanded if expanded != raw_name else None,
            },
            "candidates": [
                {"name": s["meta"]["canonical_name"], "pid": s["pid"], "score": s["score"]}
                for s in scored[:5]
            ],
            "method": method,
        }

    # ── Public: resolve_for_ingestion ─────────────────────────────────────────
    def resolve_for_ingestion(self, raw_name: str, team: str | None = None) -> dict | None:
        """Drop-in replacement for IdentityEngine.resolve_for_ingestion."""
        ctx = {"prefer_country": team} if team else {}
        result = self.resolve(raw_name, context=ctx)
        resolved = result.get("resolved")
        if not resolved:
            return None
        return {
            **resolved,
            "ambiguous": len(result.get("candidates", [])) > 1,
        }


# ─── Singleton ────────────────────────────────────────────────────────────────
_ENGINE_INSTANCE: LLMIdentityEngine | None = None

def get_engine() -> LLMIdentityEngine:
    global _ENGINE_INSTANCE
    if _ENGINE_INSTANCE is None:
        _ENGINE_INSTANCE = LLMIdentityEngine()
    return _ENGINE_INSTANCE


# ─── CLI ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="LLM Identity Engine")
    parser.add_argument("--query",   type=str, required=True, help="Player name or alias")
    parser.add_argument("--metric",  type=str, default=None,  help="e.g. 'Economy Rate'")
    parser.add_argument("--phase",   type=str, default=None,  help="e.g. 'Death'")
    parser.add_argument("--country", type=str, default=None,  help="Preferred country")
    parser.add_argument("--year",    type=int, default=None,  help="as_of_year for temporal scoring")
    args = parser.parse_args()

    engine = LLMIdentityEngine()
    ctx: dict = {}
    if args.metric:  ctx["metric"]          = args.metric
    if args.phase:   ctx["match_phase"]     = args.phase
    if args.country: ctx["prefer_country"]  = args.country
    if args.year:    ctx["as_of_date"]      = f"{args.year}-12-31"

    result = engine.resolve(args.query, context=ctx)
    print(json.dumps(result, indent=2))
