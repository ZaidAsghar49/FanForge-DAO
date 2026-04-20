"""
identity_engine.py  — Upgraded with RapidFuzz + Graceful Ambiguity
===================================================================
Drop-in replacement for the original identity_engine.py.

Key changes vs original:
  • replaces difflib with RapidFuzz (10-50× faster, better accuracy)
  • ambiguous matches no longer crash ingestion — the engine picks the
    best candidate using team  / country context when available
  • exposes resolve_for_ingestion(name, team) which ALWAYS returns a
    single best-guess result (needed for cricsheet_ingestion_engine.py)
  • still exposes .resolve(query) for the original NLP pipeline
"""

import json
import os
import re
import sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz, process as rf_process

# ─── Config ───────────────────────────────────────────────────────────────────
# scripts/identity/ → scripts/ → project root → Dataset/Players/
ROOT            = Path(__file__).resolve().parents[2]
PLAYERS_DB_FILE = ROOT / "Dataset" / "Players" / "players_data_with_all_info.csv"
FUZZY_CUTOFF    = 80   # token_set_ratio threshold (0-100)


class IdentityEngine:
    def __init__(self):
        self.players_db: pd.DataFrame = pd.DataFrame()
        self.lookup_map: dict[str, list[str]] = defaultdict(list)  # variant_lower → [pid, …]
        self.lookup_by_len: dict[int, list[str]] = defaultdict(list) # word_count -> [variants]
        self._metadata_rows: dict[str, pd.Series] = {} # pid -> Series cache
        self._fuzzy_cache: dict[str, list[str]] = {} # segment -> [pids]
        self.full_names: list[str] = []
        self._load_db()

    # ── DB Loading ────────────────────────────────────────────────────────────
    def _load_db(self):
        if not PLAYERS_DB_FILE.exists():
            print(f"[IdentityEngine] ERROR: {PLAYERS_DB_FILE} not found.")
            return
        try:
            df = pd.read_csv(PLAYERS_DB_FILE, dtype={"id": str})
            self.players_db = df
            self._build_lookup(df)
        except Exception as e:
            print(f"[IdentityEngine] DB Load Error: {e}")

    def _build_lookup(self, df: pd.DataFrame):
        for _, row in df.iterrows():
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

            for v in variants:
                if pid not in self.lookup_map[v]:
                    self.lookup_map[v].append(pid)
                    # Precompute length index
                    wc = len(v.split())
                    if v not in self.lookup_by_len[wc]:
                        self.lookup_by_len[wc].append(v)

        self.full_names = df["fullname"].dropna().unique().tolist()
        # Pre-cache metadata rows for O(1) access
        for _, row in df.iterrows():
            self._metadata_rows[str(row["id"])] = row

    # ── Metadata Fetch ────────────────────────────────────────────────────────
    @lru_cache(maxsize=4096)
    def _get_player_metadata(self, pid: str) -> dict:
        row = self._metadata_rows.get(pid)
        if row is None:
            return {}

        raw_batting_style = str(row.get("battingstyle", "")).strip()
        raw_bowling_style = str(row.get("bowlingstyle", "")).strip()

        b_style = raw_bowling_style.lower()
        if any(k in b_style for k in ["spin", "break", "orthodox", "chinaman"]):
            b_type = "Spin"
        elif any(k in b_style for k in ["fast", "medium", "pace", "seam"]):
            b_type = "Pace"
        else:
            b_type = "Unknown"

        bat_style_l = raw_batting_style.lower()
        batter_hand = (
            "Left" if "left" in bat_style_l else ("Right" if "right" in bat_style_l else "Unknown")
        )

        bow_style_l = raw_bowling_style.lower()
        bowler_hand = (
            "Left" if "left" in bow_style_l else ("Right" if "right" in bow_style_l else "Unknown")
        )

        return {
            "canonical_name":  str(row.get("fullname", "")).strip(),
            "player_id":       pid,
            "country":         str(row.get("country_name", "")).strip(),
            "primary_role":    str(row.get("position", "Unknown")).strip(),
            "batting_style":   raw_batting_style if raw_batting_style else "Unknown",
            "bowling_style":   raw_bowling_style if raw_bowling_style else "Unknown",
            "bowling_type":    b_type,
            "batter_hand":     batter_hand,
            "bowler_hand":     bowler_hand,
        }

    def resolve_for_query(self, raw_name: str) -> dict:
        """
        Query-layer resolution enforcing thresholding and returning candidates
        if ambiguous.
        """
        if not raw_name or not raw_name.strip():
            return {"resolved": None, "candidates": []}

        clean = raw_name.strip().lower()
        tokens = re.split(r"\W+", clean)
        n = len(tokens)

        for window in range(min(4, n), 0, -1):
            for i in range(n - window + 1):
                segment = " ".join(tokens[i: i + window])
                pids = self._lookup_segment(segment)
                if not pids:
                    continue

                if len(pids) == 1:
                    meta = self._get_player_metadata(pids[0])
                    return {"resolved": {**meta, "confidence": 1.0}}

                # Multiple candidates -> Score them and return
                candidates = []
                for pid in pids:
                    meta = self._get_player_metadata(pid)
                    # Compute crude fuzzy score
                    from rapidfuzz import fuzz
                    score = fuzz.token_set_ratio(clean, meta["canonical_name"].lower()) / 100.0
                    candidates.append({"name": meta["canonical_name"], "score": score, "meta": meta})
                
                candidates.sort(key=lambda x: x["score"], reverse=True)
                top_score = candidates[0]["score"]
                
                if top_score < 0.85:
                    return {"resolved": None, "status": "needs_disambiguation", "candidates": candidates}
                
                return {"resolved": {**candidates[0]["meta"], "confidence": top_score}}
                
        return {"resolved": None, "candidates": []}

    # ── Core Resolution ───────────────────────────────────────────────────────
    def _lookup_segment(self, segment: str) -> list[str]:
        """Return list of pids for a name segment (exact or fuzzy)."""
        clean = segment.strip().lower()
        if clean in self.lookup_map:
            return self.lookup_map[clean]
        
        if clean in self._fuzzy_cache:
            return self._fuzzy_cache[clean]

        # RapidFuzz fuzzy match over lookup keys with same word-count (optimized search space)
        word_count = len(clean.split())
        candidates = self.lookup_by_len.get(word_count, [])
        if not candidates:
            self._fuzzy_cache[clean] = []
            return []

        best = rf_process.extractOne(
            clean,
            candidates,
            scorer=fuzz.token_set_ratio,
            score_cutoff=FUZZY_CUTOFF,
        )
        pids = self.lookup_map[best[0]] if best else []
        self._fuzzy_cache[clean] = pids
        return pids

    def _pick_best_for_team(self, pids: list[str], team: str | None) -> str | None:
        """
        When multiple players share a name variant, pick the one whose
        country_name matches the given team string.
        Falls back to the first entry if team is unknown.
        """
        if not team:
            return pids[0]  # no context → take first (may be wrong, logged)

        team_lower = team.strip().lower()
        for pid in pids:
            meta = self._get_player_metadata(pid)
            if meta.get("country", "").lower() == team_lower:
                return pid

        # Try partial match (e.g. "South Africa" ≈ "africa")
        for pid in pids:
            meta = self._get_player_metadata(pid)
            country = meta.get("country", "").lower()
            if team_lower in country or country in team_lower:
                return pid

        return pids[0]

    # ── Public API: resolve_for_ingestion ─────────────────────────────────────
    def resolve_for_ingestion(self, raw_name: str, team: str | None = None) -> dict | None:
        """
        Always returns a single best-guess resolution dict (or None on total failure).
        Used by cricsheet_ingestion_engine.py — never raises an exception.

        Returns:
          {
            "player_id":      str,
            "canonical_name": str,
            "country":        str,
            "bowling_type":   str,
            "ambiguous":      bool,   # True if we had to guess
            "confidence":     float,
          }
        """
        if not raw_name or not raw_name.strip():
            return None

        clean = raw_name.strip().lower()
        tokens = re.split(r"\W+", clean)
        n = len(tokens)

        for window in range(min(4, n), 0, -1):
            for i in range(n - window + 1):
                segment = " ".join(tokens[i: i + window])
                pids    = self._lookup_segment(segment)
                if not pids:
                    continue

                if len(pids) == 1:
                    meta = self._get_player_metadata(pids[0])
                    return {**meta, "ambiguous": False, "confidence": 1.0}

                # Multiple candidates → try team disambiguation
                best_pid = self._pick_best_for_team(pids, team)
                meta     = self._get_player_metadata(best_pid)
                return {**meta, "ambiguous": True, "confidence": 0.6}

        return None

    # ── Public API: resolve (original NLP pipeline) ───────────────────────────
    def resolve(self, query: str) -> dict:
        """
        Original broad resolver — detects all players mentioned in a query.
        Returns the same dict structure as the old identity_engine.py.
        """
        clean  = query.lower()
        tokens = re.split(r"\W+", clean)
        n      = len(tokens)
        used   = set()
        found  = []
        status = "complete"
        notes  = []

        for window in range(4, 0, -1):
            for i in range(n - window + 1):
                indices = set(range(i, i + window))
                if not indices.isdisjoint(used):
                    continue

                segment = " ".join(tokens[i: i + window])
                pids    = self._lookup_segment(segment)
                if not pids:
                    continue

                used.update(indices)

                if len(pids) == 1:
                    meta = self._get_player_metadata(pids[0])
                    found.append({
                        "input_name": segment,
                        **meta,
                        "confidence": 1.0,
                        "ambiguous":  False,
                    })
                else:
                    for pid in pids:
                        meta = self._get_player_metadata(pid)
                        found.append({
                            "input_name": segment,
                            **meta,
                            "confidence": round(1.0 / len(pids), 3),
                            "ambiguous":  True,
                        })
                    status = "ambiguous"
                    notes.append(f"Ambiguous: '{segment}' → {len(pids)} candidates")

        if not found:
            status = "failed"
            notes.append("No players detected.")

        return {
            "players_detected": found,
            "mapping_status":   status,
            "notes":            "; ".join(notes) if notes else None,
        }


# ─── CLI ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    engine = IdentityEngine()
    if len(sys.argv) > 1:
        q = " ".join(sys.argv[1:])
        print(json.dumps(engine.resolve(q), indent=2))
    else:
        print("Testing resolve_for_ingestion:")
        for name, team in [
            ("SC Cook", "South Africa"),
            ("DA Warner", "Australia"),
            ("JM Bird", "Australia"),
            ("V Kohli", "India"),
            ("SPD Smith", "Australia"),
        ]:
            res = engine.resolve_for_ingestion(name, team=team)
            status = "✅ RESOLVED" if res and not res.get("ambiguous") else \
                     "⚠️  GUESSED " if res else "❌ FAILED  "
            print(f"  {status}  {name:<20} → {res['canonical_name'] if res else 'None'}")
