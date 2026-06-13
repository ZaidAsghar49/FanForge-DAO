"""
identity_engine.py  —  Resilient 10-Tier Cascading Identity Engine
=================================================================
Cricket-specific player name-matching system mapping raw scorecard strings
to the Canonical Players Registry using a tiered, multi-pass decision tree.
"""

import json
import os
import re
import sqlite3
import unicodedata
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz

# ─── Config ───────────────────────────────────────────────────────────────────
ROOT            = Path(__file__).resolve().parents[2]
PLAYERS_DB_FILE = ROOT / "Dataset" / "Players" / "players_data_with_all_info.csv"
CACHE_FILE      = ROOT / "data" / "scorecard_aliases_cache.json"

STATIC_EDGE_CASES = {
    "rg sharma": "Rohit Sharma",
    "surya": "Suryakumar Yadav",
    "sky": "Suryakumar Yadav",
    "ms dhoni": "Mahendra Singh Dhoni",
    "dhoni": "Mahendra Singh Dhoni",
    "abd": "AB de Villiers",
    "ab de villiers": "AB de Villiers",
}

class IdentityEngine:
    def __init__(self, db_path=None):
        self.players_db: pd.DataFrame = pd.DataFrame()
        self.players_list: list[dict] = []                         # Fast dict-based lookup candidates
        self.lookup_map: dict[str, list[str]] = defaultdict(list)  # variant_lower → [pid, …]
        self._metadata_rows: dict[str, dict] = {}                  # pid → dict metadata cache
        self.full_names: list[str] = []
        self.active_pids: set[str] = set()                         # Tier 4 temporal candidates
        self.cache: dict[str, list[str]] = {}                      # Tier 1 cached lookup
        self.alias_to_canonical: dict[str, str] = {}               # Tier 1 reverse cache

        self._load_db()
        self._load_cache()
        self._load_active_players(db_path)

    # ── Database Loading ──────────────────────────────────────────────────────
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
        self.players_list = []
        for _, row in df.iterrows():
            pid   = str(row["id"])
            fname = str(row.get("fullname",  "")).strip()
            first = str(row.get("firstname", "")).strip()
            last  = str(row.get("lastname",  "")).strip()

            if not fname or fname == "nan":
                continue

            variants = set()
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

            # Pre-compute fields for fast lookup
            country_name = str(row.get("country_name", "")).strip()
            country_lower = country_name.lower()
            
            # Pre-calculate phonetic signature for the surname
            lastname_lower = last.lower() if last and last != "nan" else ""
            lastname_phonetic = self._phonetic_signature(lastname_lower) if lastname_lower else ""

            player_dict = {
                "id": pid,
                "fullname": fname,
                "fullname_lower": fname.lower(),
                "firstname": first,
                "firstname_lower": first.lower(),
                "lastname": last,
                "lastname_lower": lastname_lower,
                "lastname_phonetic": lastname_phonetic,
                "country_name": country_name,
                "country_lower": country_lower,
            }
            self.players_list.append(player_dict)

            # Build metadata cache directly as dict
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
            batter_hand = "Left" if "left" in bat_style_l else ("Right" if "right" in bat_style_l else "Unknown")

            bow_style_l = raw_bowling_style.lower()
            bowler_hand = "Left" if "left" in bow_style_l else ("Right" if "right" in bow_style_l else "Unknown")

            self._metadata_rows[pid] = {
                "canonical_name":  fname,
                "player_id":       pid,
                "country":         country_name,
                "primary_role":    str(row.get("position", "Unknown")).strip(),
                "batting_style":   raw_batting_style if raw_batting_style else "Unknown",
                "bowling_style":   raw_bowling_style if raw_bowling_style else "Unknown",
                "bowling_type":    b_type,
                "batter_hand":     batter_hand,
                "bowler_hand":     bowler_hand,
            }

        self.full_names = df["fullname"].dropna().unique().tolist()

    # ── Cache Loading ─────────────────────────────────────────────────────────
    def _load_cache(self):
        if CACHE_FILE.exists():
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    self.cache = json.load(f)
                
                # Invert cache for O(1) lookup: alias_lower -> canonical_name
                self.alias_to_canonical = {}
                for key, aliases in self.cache.items():
                    parts = key.split(":")
                    if len(parts) >= 2:
                        canonical = parts[1]
                        for alias in aliases:
                            self.alias_to_canonical[alias.strip().lower()] = canonical
            except Exception as e:
                print(f"[IdentityEngine] Cache load warning: {e}")

    # ── Temporal Era Filtering (Active Player Era 2019-2026) ──────────────────
    def _load_active_players(self, db_path=None):
        if db_path is None:
            db_path = ROOT / "cricket.db"
            if not db_path.exists():
                db_path = ROOT / "cricket_india.db"
        else:
            from pathlib import Path
            db_path = Path(db_path)

        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()
                cursor.execute("SELECT DISTINCT batter FROM deliveries")
                active_names = {row[0].strip().lower() for row in cursor.fetchall() if row[0]}
                cursor.execute("SELECT DISTINCT bowler FROM deliveries")
                active_names.update(row[0].strip().lower() for row in cursor.fetchall() if row[0])
                conn.close()

                # Map active scorecard names to registry player IDs
                for name in active_names:
                    # check exact matching variant keys
                    pids = self.lookup_map.get(name)
                    if pids:
                        self.active_pids.update(pids)
                    else:
                        # Fallback simple split checks
                        last = name.split()[-1]
                        pids = self.lookup_map.get(last)
                        if pids:
                            self.active_pids.update(pids)
            except Exception as e:
                print(f"[IdentityEngine] Temporal loading warning: {e}")

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _strip_diacritics(self, s: str) -> str:
        if not s:
            return ""
        return "".join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))

    def _phonetic_signature(self, s: str) -> str:
        """Simplified metaphone algorithm to group homophonic consonant profiles."""
        if not s:
            return ""
        s = self._strip_diacritics(s).upper()
        words = s.split()
        sigs = []
        for word in words:
            if not word:
                continue
            # Remove duplicates
            collapsed = []
            for char in word:
                if not collapsed or collapsed[-1] != char:
                    collapsed.append(char)
            w = "".join(collapsed)
            
            # Group equivalent sounding consonants and strip vowels
            w = w.replace("PH", "F").replace("TH", "T")
            
            if not w:
                continue
            first = w[0]
            rest = w[1:]
            
            mapping = {
                'B': 'P', 'D': 'T', 'G': 'K', 'J': 'K', 'Q': 'K', 'Z': 'S', 'V': 'F',
                'A': '', 'E': '', 'I': '', 'O': '', 'U': '', 'H': '', 'W': '', 'Y': ''
            }
            res_rest = [mapping.get(char, char) for char in rest]
            sigs.append(first + "".join(res_rest))
        return " ".join(sigs)

    def _match_initials(self, initials_str: str, canonical_fullname: str) -> bool:
        """Matches leading initials to candidate first/middle name tokens sequentially."""
        clean_initials = "".join(c for c in initials_str if c.isalpha()).upper()
        if not clean_initials:
            return False
            
        candidate_tokens = [t.upper() for t in canonical_fullname.split()[:-1]]
        if len(candidate_tokens) < len(clean_initials):
            return False
            
        for i, char in enumerate(clean_initials):
            if not candidate_tokens[i].startswith(char):
                return False
        return True

    def _get_player_metadata(self, pid: str) -> dict:
        # Returns pre-computed metadata dictionary directly, or empty dict if not found
        return self._metadata_rows.get(pid, {})

    # ─── CORE 10-TIER CASCADE DECISION TREE ───────────────────────────────────
    def resolve_player_identity(self, raw_name: str, context: dict | None = None) -> str | None:
        """
        Core cascading identity resolution engine mapping raw string to a canonical pid.
        Returns canonical player_id (str) or None.
        """
        if not raw_name or not isinstance(raw_name, str):
            return None

        context = context or {}
        prefer_role = (context.get("prefer_role") or "").lower()
        active_teams = context.get("active_teams") or []
        team_hint = context.get("team_hint")
        if team_hint and team_hint not in active_teams:
            active_teams.append(team_hint)
        active_teams = [t.lower().strip() for t in active_teams if t]

        # Tier 1: Static Alias Map & Exact Cache Check
        clean = raw_name.strip().lower()
        if clean in self.alias_to_canonical:
            canon = self.alias_to_canonical[clean]
            pids = self.lookup_map.get(canon.lower())
            if pids:
                return pids[0]

        if clean in STATIC_EDGE_CASES:
            target = STATIC_EDGE_CASES[clean]
            pids = self.lookup_map.get(target.lower())
            if pids:
                return pids[0]

        # Tier 2: Structural Diacritic Stripping
        clean_no_dia = self._strip_diacritics(clean)
        pids = self.lookup_map.get(clean_no_dia)
        if pids:
            return self._tie_breaker(pids, active_teams, prefer_role)

        # Tier 3: Surname-First Inversion Check
        if "," in clean_no_dia:
            parts = clean_no_dia.split(",")
            inverted = f"{parts[1].strip()} {parts[0].strip()}"
            pids = self.lookup_map.get(inverted)
            if pids:
                return self._tie_breaker(pids, active_teams, prefer_role)
            clean_no_dia = inverted

        # Prep candidates list
        candidates = self.players_list
        # Tier 4: Temporal Candidate Pruning
        if self.active_pids:
            # Prune candidates pool strictly to active players if pool is populated
            pruned = [p for p in candidates if p["id"] in self.active_pids]
            if pruned:
                candidates = pruned

        # Tier 5: Cross-National Match Metadata Filtering
        if active_teams:
            active_teams_set = set(active_teams)
            filtered = [p for p in candidates if p["country_lower"] in active_teams_set]
            if filtered:
                candidates = filtered

        # Tier 6: Initials-Expansion Regex Heuristic
        tokens = clean_no_dia.split()
        if len(tokens) >= 2:
            last_token = tokens[-1]
            initials_token = tokens[0]
            if len(initials_token) <= 4 and all(c.isalpha() for c in initials_token):
                # Candidate lastname must match exactly
                for p in candidates:
                    if p["lastname_lower"] == last_token:
                        if self._match_initials(initials_token, p["fullname"]):
                            return p["id"]

        # Tier 7: Multi-Token Weighting (Last Name Primacy)
        best_pid = None
        best_score = 0.0
        for p in candidates:
            pid = p["id"]
            fullname_lower = p["fullname_lower"]
            lastname_lower = p["lastname_lower"]
            
            raw_tokens = clean_no_dia.split()
            canon_tokens = fullname_lower.split()
            if not raw_tokens or not canon_tokens:
                continue
            
            raw_last = raw_tokens[-1]
            surname_similarity = fuzz.token_set_ratio(raw_last, lastname_lower)
            
            # Surname similarity cutoff threshold
            if surname_similarity < 85:
                continue

            raw_leading = " ".join(raw_tokens[:-1])
            canon_leading = " ".join(canon_tokens[:-1])
            leading_similarity = fuzz.token_set_ratio(raw_leading, canon_leading) if raw_leading else 100.0
            
            weighted_score = (0.70 * surname_similarity) + (0.30 * leading_similarity)
            if weighted_score > best_score:
                best_score = weighted_score
                best_pid = pid

        if best_pid and best_score >= 90.0:
            return best_pid

        # Tier 8: Double Metaphone Phonetic Matching
        raw_phonetic_last = self._phonetic_signature(clean_no_dia.split()[-1]) if clean_no_dia.split() else ""
        if raw_phonetic_last:
            for p in candidates:
                pid = p["id"]
                fullname_lower = p["fullname_lower"]
                canon_phonetic_last = p["lastname_phonetic"]
                if raw_phonetic_last == canon_phonetic_last:
                    # Verify initials check to avoid collisions
                    if len(clean_no_dia.split()) >= 2:
                        first_init = clean_no_dia.split()[0][0]
                        if fullname_lower.startswith(first_init):
                            return pid
                    else:
                        return pid

        # Tier 9: Dynamic Metric/Role Validation (Tie-breaker logic fallback)
        # Already incorporated via self._tie_breaker during Tier 2-3 collisions.
        # Fallback to general lookup
        pids = self.lookup_map.get(clean_no_dia)
        if pids:
            return self._tie_breaker(pids, active_teams, prefer_role)

        return None

    def _tie_breaker(self, pids: list[str], active_teams: list[str], prefer_role: str) -> str:
        """Resolve collisions between multiple resolved pids using metadata."""
        if len(pids) == 1:
            return pids[0]

        scored_candidates = []
        for pid in pids:
            meta = self._get_player_metadata(pid)
            score = 0.0

            # Match country to competing teams
            country = str(meta.get("country", "")).lower().strip()
            if country in active_teams:
                score += 10.0

            # Dynamic role validation
            role = str(meta.get("primary_role", "")).lower()
            if prefer_role == "bowling":
                if "bowler" in role or meta.get("bowling_type") in ("Spin", "Pace"):
                    score += 5.0
            elif prefer_role == "batting":
                if "batsman" in role or "allrounder" in role:
                    score += 5.0

            scored_candidates.append((score, pid))

        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        return scored_candidates[0][1]

    # ─── Public API mappings ───────────────────────────────────────────────────
    def resolve_for_ingestion(self, raw_name: str, team: str | None = None) -> dict | None:
        """Ingestion-layer interface mapping to full metadata structure."""
        ctx = {"team_hint": team}
        pid = self.resolve_player_identity(raw_name, ctx)
        if pid:
            meta = self._get_player_metadata(pid)
            return {
                "player_id":      meta["player_id"],
                "canonical_name": meta["canonical_name"],
                "country":        meta["country"],
                "bowling_type":   meta["bowling_type"],
                "batting_style":  meta["batting_style"],
                "bowling_style":  meta["bowling_style"],
                "ambiguous":      False,
                "confidence":     1.0,
            }
        return None

    def resolve_for_query(self, raw_name: str, context: dict | None = None) -> dict:
        """Query-layer interface mapping returns resolved dict structure."""
        context = context or {}
        pid = self.resolve_player_identity(raw_name, context)
        if pid:
            meta = self._get_player_metadata(pid)
            return {"resolved": {**meta, "confidence": 1.0}}
        return {"resolved": None, "candidates": []}

    def resolve(self, query: str, context: dict | None = None) -> dict:
        """Broad NLP query parser mapping interface supporting multiple schemas."""
        context = context or {}
        prefer_country = context.get("prefer_country")
        if prefer_country:
            active_teams = context.get("active_teams") or []
            if prefer_country not in active_teams:
                active_teams.append(prefer_country)
            context["active_teams"] = active_teams

        # Map query to raw_name for player resolution
        pid = self.resolve_player_identity(query, context)
        
        # Build candidates list for validate_model compatibility
        candidates = []
        try:
            from rapidfuzz import process as rf_process
            names = [p["fullname"] for p in self.players_list]
            matches = rf_process.extract(query, names, limit=5, scorer=fuzz.token_set_ratio)
            for name, score, idx in matches:
                if score >= 70:
                    p = self.players_list[idx]
                    candidates.append({
                        "name": p["fullname"],
                        "pid": p["id"],
                        "score": score,
                        "meta": self._get_player_metadata(p["id"])
                    })
        except Exception:
            pass

        found = []
        status = "failed"
        notes = []
        resolved = None

        if pid:
            meta = self._get_player_metadata(pid)
            resolved = {**meta, "confidence": 1.0, "ambiguous": False}
            found.append({
                "input_name": query,
                **meta,
                "confidence": 1.0,
                "ambiguous":  False,
            })
            status = "complete"
        else:
            notes.append("No players detected.")

        # If we failed to resolve but have multiple candidates, we can report needs_disambiguation
        model_status = status
        if not pid and len(candidates) > 1:
            model_status = "needs_disambiguation"

        return {
            # validate_model.py keys:
            "resolved": resolved,
            "candidates": candidates,
            "status": model_status,
            
            # cricsheet_ingestion_engine.py keys:
            "players_detected": found,
            "mapping_status": status,
            "notes": "; ".join(notes) if notes else None,
        }

    # ── Tier 10: Bootstrap Cache Warm-Up Routine ──────────────────────────────
    def bootstrap_resolution_cache(self, db_conn: sqlite3.Connection):
        """Warm up scorecard cache by pre-resolving distinct names from database."""
        print("[IdentityEngine] Starting Bootstrap Cache Warm-Up Routine...")
        cursor = db_conn.cursor()
        
        # Get distinct batters and bowlers
        cursor.execute("SELECT DISTINCT batter FROM deliveries")
        batters = [row[0] for row in cursor.fetchall() if row[0]]
        cursor.execute("SELECT DISTINCT bowler FROM deliveries")
        bowlers = [row[0] for row in cursor.fetchall() if row[0]]

        warm_up_mappings = defaultdict(set)

        # Warm up batter aliases
        total_batters = len(batters)
        print(f"[IdentityEngine] Resolving {total_batters} distinct batters...")
        for idx, name in enumerate(batters):
            if idx % 1000 == 0:
                print(f"[IdentityEngine]   Resolved {idx}/{total_batters} batters...")
            pid = self.resolve_player_identity(name, {"prefer_role": "batting"})
            if pid:
                meta = self._get_player_metadata(pid)
                key = f"batter:{meta['canonical_name']}"
                warm_up_mappings[key].add(name)

        # Warm up bowler aliases
        total_bowlers = len(bowlers)
        print(f"[IdentityEngine] Resolving {total_bowlers} distinct bowlers...")
        for idx, name in enumerate(bowlers):
            if idx % 1000 == 0:
                print(f"[IdentityEngine]   Resolved {idx}/{total_bowlers} bowlers...")
            pid = self.resolve_player_identity(name, {"prefer_role": "bowling"})
            if pid:
                meta = self._get_player_metadata(pid)
                key = f"bowler:{meta['canonical_name']}"
                warm_up_mappings[key].add(name)

        # Update cache format
        for key, aliases in warm_up_mappings.items():
            self.cache.setdefault(key, [])
            for alias in aliases:
                if alias not in self.cache[key]:
                    self.cache[key].append(alias)

        # Save to disk
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=2)
            print(f"[IdentityEngine] Warm-up complete! Wrote {len(self.cache)} entries to {CACHE_FILE}")
            
            # Reload reverse cache
            self.alias_to_canonical = {}
            for key, aliases in self.cache.items():
                parts = key.split(":")
                if len(parts) >= 2:
                    canonical = parts[1]
                    for alias in aliases:
                        self.alias_to_canonical[alias.strip().lower()] = canonical
        except Exception as e:
            print(f"[IdentityEngine] Error saving bootstrap cache: {e}")

if __name__ == "__main__":
    engine = IdentityEngine()
    db_path = ROOT / "Dataset" / "Processed" / "cricket_clean_38.db"
    if not db_path.exists():
        db_path = ROOT / "cricket.db"
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        engine.bootstrap_resolution_cache(conn)
        conn.close()
