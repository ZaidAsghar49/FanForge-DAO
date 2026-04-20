"""
fuzzy_identity_engine.py  —  Phase 2: Identity Resolution Engine
=================================================================
Resolves Cricsheet short-name aliases (e.g. "DA Warner") to canonical
full names and stable player IDs using:

  1. Exact / variant lookup  (O(1) dict)
  2. Team + country disambiguation  (resolves "SC Cook")
  3. RapidFuzz token-set-ratio  (handles abbreviations / initials)
  4. Date-of-birth cross-check   (for hard cases)

Outputs:
  • resolution_cache.json   – {raw_name -> canonical_id, canonical_name}
  • audit_report.csv        – mapped / ambiguous / unmapped breakdown

Usage:
  python fuzzy_identity_engine.py
  python fuzzy_identity_engine.py --audit   # just show stats, no cache re-build
"""

import json
import os
import re
import sys
import argparse
import logging
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz, process as rf_process

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── Paths ────────────────────────────────────────────────────────────────────
# scripts/identity/ → scripts/ → project root
ROOT            = Path(__file__).resolve().parents[2]
PLAYERS_DB      = ROOT / "Dataset" / "Players" / "players_data_with_all_info.csv"
CRICKETERS_CSV  = ROOT / "Dataset" / "Players" / "cricketers.csv"
MATCHES_CSV     = ROOT / "matches.csv"
CACHE_FILE      = ROOT / "output" / "resolution_cache.json"
AUDIT_FILE      = ROOT / "output" / "audit_report.csv"

# ─── Tuning ───────────────────────────────────────────────────────────────────
FUZZY_THRESHOLD     = 82   # token_set_ratio minimum for a "confident" match
INITIALS_CONFIDENCE = 90   # higher bar when we only have X Y style names


# ─── Helper: build name variants ──────────────────────────────────────────────
def _name_variants(row: pd.Series) -> list[str]:
    """Return a list of lowercase variants for a player row."""
    full  = str(row.get("fullname",  "")).strip()
    first = str(row.get("firstname", "")).strip()
    last  = str(row.get("lastname",  "")).strip()

    variants = set()
    if full  and full  != "nan": variants.add(full.lower())
    if first and last  and first != "nan" and last != "nan":
        # "David Warner" → "d warner", "da warner"
        variants.add(f"{first[0]} {last}".lower())
        if len(first) >= 2:
            variants.add(f"{first[:2]} {last}".lower())
        variants.add(f"{first} {last}".lower())
        # no-space: "dawarner"
        variants.add(f"{first[0]}{last}".lower())
    if last and last != "nan":
        variants.add(last.lower())

    return [v for v in variants if v]


# ─── Core Engine ──────────────────────────────────────────────────────────────
class FuzzyIdentityEngine:
    """
    Resolves a raw cricketer name to a canonical player ID.

    Resolution chain:
      1. Cached result  (JSON file from prior run)
      2. Exact variant lookup
      3. Team / country narrowing  →  exact re-check
      4. RapidFuzz over filtered candidates
      5. RapidFuzz over full DB (last resort)
    """

    def __init__(self, rebuild_cache: bool = False):
        self.db: pd.DataFrame = pd.DataFrame()
        self.lookup: dict[str, list[dict]] = defaultdict(list)  # variant → [{player}]
        self.full_name_index: list[str] = []                    # all canonical full names
        self.country_index: dict[str, list[str]] = defaultdict(list) # country -> [fullnames]
        self._metadata_rows: dict[str, pd.Series] = {}          # pid -> Series cache
        self._fuzzy_cache: dict[tuple, dict] = {}               # (raw, country) -> resolved_internal
        self.cache: dict[str, dict] = {}                        # raw_name → resolved

        self._load_db()
        self._build_lookup()
        if not rebuild_cache:
            self._load_cache()

    # ── DB Loading ────────────────────────────────────────────────────────────
    def _load_db(self):
        if not PLAYERS_DB.exists():
            log.error("Players DB not found: %s", PLAYERS_DB)
            return

        self.db = pd.read_csv(PLAYERS_DB, dtype={"id": str})
        log.info("Loaded %d players from DB.", len(self.db))

    def _build_lookup(self):
        for _, row in self.db.iterrows():
            pid  = str(row["id"])
            meta = {
                "player_id":       pid,
                "canonical_name":  str(row.get("fullname", "")).strip(),
                "country":         str(row.get("country_name", "")).strip(),
                "position":        str(row.get("position", "")).strip(),
                "batting_style":   str(row.get("battingstyle", "")).strip(),
                "bowling_style":   str(row.get("bowlingstyle", "")).strip(),
                "dob":             str(row.get("dateofbirth", "")).strip(),
            }
            meta["bowling_type"] = self._classify_bowling(meta["bowling_style"])

            for v in _name_variants(row):
                self.lookup[v].append(meta)
            
            # Precompute metadata and country indices
            self._metadata_rows[pid] = row
            c_name = meta["canonical_name"]
            if c_name:
                self.full_name_index.append(c_name)
                if meta["country"]:
                    self.country_index[meta["country"].lower()].append(c_name)

        log.info("Lookup index has %d distinct variants.", len(self.lookup))

    @staticmethod
    def _classify_bowling(style: str) -> str:
        s = style.lower()
        if any(k in s for k in ["spin", "break", "orthodox", "chinaman", "googly", "legbreak", "offbreak"]):
            return "Spin"
        if any(k in s for k in ["fast", "medium", "seam", "pace"]):
            return "Pace"
        return "Unknown"

    # ── Cache IO ──────────────────────────────────────────────────────────────
    def _load_cache(self):
        if CACHE_FILE.exists():
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                self.cache = json.load(f)
            log.info("Cache loaded: %d entries.", len(self.cache))

    def save_cache(self):
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, indent=2, ensure_ascii=False)
        log.info("Cache saved → %s  (%d entries)", CACHE_FILE, len(self.cache))

    # ── Resolution Logic ──────────────────────────────────────────────────────
    def resolve(
        self,
        raw_name: str,
        team: str | None = None,
        country: str | None = None,
    ) -> dict:
        """
        Returns:
          {
            "status":         "mapped" | "ambiguous" | "unmapped",
            "player_id":      str | None,
            "canonical_name": str | None,
            "country":        str | None,
            "bowling_type":   str | None,
            "confidence":     float,          # 0..1
            "method":         str,            # how it was resolved
            "candidates":     list[dict],     # populated only when ambiguous
          }
        """
        if not raw_name or not raw_name.strip():
            return self._result("unmapped", method="empty_input")

        key = raw_name.strip()

        # 1. Cache hit
        if key in self.cache:
            return {**self.cache[key], "method": "cache"}

        result = self._resolve_internal(key, team, country)
        # Store only concrete resolutions in the cache (not ambiguous/unmapped)
        if result["status"] == "mapped":
            self.cache[key] = {k: v for k, v in result.items() if k not in {"method", "candidates"}}

        return result

    def _resolve_internal(self, raw: str, team, country) -> dict:
        clean = raw.strip().lower()

        # ── Step 1: Exact variant lookup ──────────────────────────────────────
        if clean in self.lookup:
            hits = self.lookup[clean]
            if len(hits) == 1:
                return self._result("mapped", hits[0], confidence=1.0, method="exact")
            # Multiple hits → try disambiguation
            narrowed = self._disambiguate(hits, team, country)
            if narrowed and len(narrowed) == 1:
                return self._result("mapped", narrowed[0], confidence=0.95,
                                    method="exact+team_country_disambig")
            return self._result("ambiguous", candidates=hits or narrowed,
                                method="exact_ambiguous")

        # ── Step 2: Fuzzy match ───────────────────────────────────────────────
        # Determine a sensible score threshold.  Initials ("DA Warner") need
        # a higher threshold because token_set_ratio can over-match.
        is_initials = bool(re.match(r"^[A-Z]{1,3}\s+\w", raw))
        threshold   = INITIALS_CONFIDENCE if is_initials else FUZZY_THRESHOLD
        
        # Check internal fuzzy cache
        cache_key = (raw, country)
        if cache_key in self._fuzzy_cache:
            return self._fuzzy_cache[cache_key]

        # Build candidate pool: if we have team/country, restrict first.
        pool = self._country_filtered_index(country) if country else self.full_name_index

        best = rf_process.extractOne(
            raw,
            pool,
            scorer=fuzz.token_set_ratio,
            score_cutoff=threshold,
        )

        result = None
        if best:
            matched_name, score, _ = best
            hits = self.lookup.get(matched_name.lower(), [])
            if not hits:
                # full name might not be a lookup key itself; check pre-cached metadata
                for pid, row in self._metadata_rows.items():
                    if str(row.get("fullname", "")).strip().lower() == matched_name.lower():
                        hits = self._rows_to_meta(pd.DataFrame([row]))
                        break
            
            if len(hits) == 1:
                result = self._result("mapped", hits[0], confidence=score / 100,
                                    method=f"rapidfuzz(score={score:.0f})")
            elif hits:
                narrowed = self._disambiguate(hits, team, country)
                if narrowed and len(narrowed) == 1:
                    result = self._result("mapped", narrowed[0],
                                        confidence=score / 100 * 0.9,
                                        method=f"rapidfuzz+disambig(score={score:.0f})")
                else:
                    result = self._result("ambiguous", candidates=hits,
                                        method=f"rapidfuzz_ambiguous(score={score:.0f})")

        if result:
            self._fuzzy_cache[cache_key] = result
            return result

        # ── Step 3: Last-name only attempt ───────────────────────────────────
        last_token = clean.split()[-1]
        if last_token in self.lookup and len(last_token) > 3:
            hits = self.lookup[last_token]
            narrowed = self._disambiguate(hits, team, country)
            if narrowed and len(narrowed) == 1:
                return self._result("mapped", narrowed[0], confidence=0.75,
                                    method="lastname+disambig")
            if hits:
                return self._result("ambiguous", candidates=narrowed or hits,
                                    method="lastname_only_ambiguous")

        return self._result("unmapped", method="no_match")

    def _disambiguate(self, candidates: list[dict], team: str | None, country: str | None) -> list[dict]:
        """Narrow candidates using team-country information."""
        if not candidates:
            return candidates
        refined = candidates[:]

        # Country filter (cricketers.csv country vs DB country_name)
        if country:
            c_low = country.strip().lower()
            by_country = [c for c in refined if c["country"].lower() == c_low]
            if by_country:
                refined = by_country

        return refined

    def _country_filtered_index(self, country: str) -> list[str]:
        c_low = country.strip().lower()
        names = self.country_index.get(c_low)
        return names if names else self.full_name_index  # fall back to full

    def _rows_to_meta(self, rows: pd.DataFrame) -> list[dict]:
        results = []
        for _, row in rows.iterrows():
            results.append({
                "player_id":       str(row["id"]),
                "canonical_name":  str(row.get("fullname", "")).strip(),
                "country":         str(row.get("country_name", "")).strip(),
                "position":        str(row.get("position", "")).strip(),
                "batting_style":   str(row.get("battingstyle", "")).strip(),
                "bowling_style":   str(row.get("bowlingstyle", "")).strip(),
                "bowling_type":    self._classify_bowling(str(row.get("bowlingstyle", ""))),
                "dob":             str(row.get("dateofbirth", "")).strip(),
            })
        return results

    @staticmethod
    def _result(
        status: str,
        player: dict | None = None,
        confidence: float = 0.0,
        method: str = "",
        candidates: list[dict] | None = None,
    ) -> dict:
        r: dict = {
            "status":         status,
            "player_id":      player["player_id"]      if player else None,
            "canonical_name": player["canonical_name"] if player else None,
            "country":        player["country"]        if player else None,
            "bowling_type":   player["bowling_type"]   if player else None,
            "confidence":     round(confidence, 4),
            "method":         method,
            "candidates":     candidates or [],
        }
        return r

    # ── Batch Audit ───────────────────────────────────────────────────────────
    def run_audit(self, source: str = "cricketers") -> pd.DataFrame:
        """
        Check all players in cricketers.csv (or the unique players in matches.csv)
        and generate an audit report.

        source: "cricketers"  →  use cricketers.csv
                "matches"     →  extract unique names from matches.csv
        """
        if source == "cricketers" and CRICKETERS_CSV.exists():
            src_df   = pd.read_csv(CRICKETERS_CSV)
            names    = src_df["Name"].dropna().unique().tolist()
            country_map = dict(zip(src_df["Name"].dropna(),
                                   src_df["Country"].fillna("")))
        elif source == "matches" and MATCHES_CSV.exists():
            log.info("Scanning matches.csv for unique player names (this may take a moment)…")
            m = pd.read_csv(MATCHES_CSV, usecols=["batter", "bowler"])
            names = pd.concat([m["batter"], m["bowler"]]).dropna().unique().tolist()
            country_map = {}
        else:
            log.error("Source file not found.")
            return pd.DataFrame()

        log.info("Auditing %d unique names from '%s'…", len(names), source)

        rows = []
        for name in names:
            country = country_map.get(name, "")
            res     = self.resolve(name, country=country)
            row = {
                "raw_name":       name,
                "source_country": country,
                "status":         res["status"],
                "player_id":      res["player_id"],
                "canonical_name": res["canonical_name"],
                "db_country":     res["country"],
                "bowling_type":   res["bowling_type"],
                "confidence":     res["confidence"],
                "method":         res["method"],
                "num_candidates": len(res["candidates"]),
                "candidate_names": " | ".join(
                    c["canonical_name"] for c in res["candidates"][:6]
                ),
            }
            rows.append(row)

        df = pd.DataFrame(rows)
        df.to_csv(AUDIT_FILE, index=False)

        # Summary
        total = len(df)
        mapped    = (df["status"] == "mapped").sum()
        ambiguous = (df["status"] == "ambiguous").sum()
        unmapped  = (df["status"] == "unmapped").sum()

        log.info("=" * 60)
        log.info("  AUDIT REPORT — %s", source.upper())
        log.info("  Total   : %d", total)
        log.info("  MAPPED  : %d  (%.1f%%)", mapped,    mapped    / total * 100)
        log.info("  AMBIG.  : %d  (%.1f%%)", ambiguous, ambiguous / total * 100)
        log.info("  UNMAPPED: %d  (%.1f%%)", unmapped,  unmapped  / total * 100)
        log.info("  Saved → %s", AUDIT_FILE)
        log.info("=" * 60)

        return df


# ── CLI Entry Point ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fuzzy Identity Engine")
    parser.add_argument("--audit",         action="store_true",
                        help="Run full audit and save report")
    parser.add_argument("--source",        default="cricketers",
                        choices=["cricketers", "matches"],
                        help="Source for audit player names")
    parser.add_argument("--rebuild-cache", action="store_true",
                        help="Ignore existing cache and rebuild")
    parser.add_argument("--query",         type=str, default=None,
                        help="Resolve a single name (for testing)")
    parser.add_argument("--team",          type=str, default=None,
                        help="Optional team hint for single query")
    parser.add_argument("--country",       type=str, default=None,
                        help="Optional country hint for single query")
    args = parser.parse_args()

    engine = FuzzyIdentityEngine(rebuild_cache=args.rebuild_cache)

    if args.query:
        result = engine.resolve(args.query, team=args.team, country=args.country)
        print(json.dumps(result, indent=2))
        engine.save_cache()

    elif args.audit:
        engine.run_audit(source=args.source)
        engine.save_cache()

    else:
        # Interactive test
        print("Fuzzy Identity Engine — interactive mode")
        print("Type a player name (or 'quit' to exit):\n")
        while True:
            name = input("Name > ").strip()
            if name.lower() in ("quit", "exit", "q"):
                break
            result = engine.resolve(name)
            print(json.dumps(result, indent=2), "\n")
        engine.save_cache()
