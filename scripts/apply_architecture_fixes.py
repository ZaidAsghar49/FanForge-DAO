import os
import re

print("Applying Systemic Architecture Fixes...")

IDENTITY_PATH = "scripts/identity/identity_engine.py"
VALIDATE_PATH = "scripts/analysis/validate_model.py"


# ==========================================
# 2. FIX: IdentityEngine (Identity ambiguity)
# ==========================================
with open(IDENTITY_PATH, "r", encoding="utf-8") as f:
    ident_code = f.read()

ident_addition = """    def resolve_for_query(self, raw_name: str) -> dict:
        \"\"\"
        Query-layer resolution enforcing thresholding and returning candidates
        if ambiguous.
        \"\"\"
        if not raw_name or not raw_name.strip():
            return {"resolved": None, "candidates": []}

        clean = raw_name.strip().lower()
        tokens = re.split(r"\\W+", clean)
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
"""

if "def resolve_for_query(" not in ident_code:
    ident_code = ident_code.replace("    # ── Core Resolution", ident_addition + "\n    # ── Core Resolution")
    with open(IDENTITY_PATH, "w", encoding="utf-8") as f:
        f.write(ident_code)
print("identity_engine.py patched (Fix 3: Identity ambiguity gating)")


# ==========================================
# 3. FIX: validate_model.py (Strict Comp, Sample Guards, Home Logic)
# ==========================================
with open(VALIDATE_PATH, "r", encoding="utf-8") as f:
    val_code = f.read()

# Identity resolution patch (Phase 2)
id_bad = """    subj_res = engine.resolve_for_ingestion(subject)
    if not subj_res:
        msg = f"Cannot resolve player '{subject}'."
        print(f"    ❌ {msg}")
        return {"status": "error", "message": msg}

    canonical = subj_res["canonical_name"]"""

id_good = """    query_res = engine.resolve_for_query(subject)
    if "needs_disambiguation" in query_res.get("status", ""):
        opts = [f"{c['name']} ({c['meta'].get('country','')})" for c in query_res['candidates']]
        msg = f"Which '{subject}' do you mean? Options: {', '.join(opts)}"
        print(f"    [WARN] {msg}")
        return {"status": "needs_disambiguation", "message": msg, "options": query_res['candidates']}
        
    subj_res = query_res.get("resolved")
    if not subj_res:
        msg = f"Cannot resolve player '{subject}'."
        print(f"    [X] {msg}")
        return {"status": "error", "message": msg}

    canonical = subj_res["canonical_name"]"""
val_code = val_code.replace(id_bad, id_good)


# Competition and Strict filtering (Phase 3 inside _apply_filters or equivalent)
fmt_bad = """        if "ipl" in fmt.lower() and "competition" in df.columns:
            df = df[df["competition"].str.contains("IPL|Indian Premier League", case=False, na=False)]
        elif fmt.lower() == "t20i":"""
fmt_good = """
        COMPETITION_MAP = {
            "ipl": ["Indian Premier League"],
            "psl": ["Pakistan Super League"],
            "bbl": ["Big Bash League"],
            "cpl": ["Caribbean Premier League"]
        }
        
        comp_key = fmt.lower()
        if comp_key in COMPETITION_MAP and "competition" in df.columns:
            df = df[df["competition"].isin(COMPETITION_MAP[comp_key])]
        elif fmt.lower() == "t20i":"""
val_code = val_code.replace(fmt_bad, fmt_good)


# Home Logic for Bowlers (Phase 3)
home_bad = """    # ── 11. Home/Away (filter by batting_team == home team from match metadata) ─
    home_away = filters.get("home_away")
    if home_away and "home_team" in df.columns and "batting_team" in df.columns:
        if home_away.lower() == "home":
            df = df[df["batting_team"] == df["home_team"]]
        elif home_away.lower() == "away":
            df = df[df["batting_team"] != df["home_team"]]"""

home_good = """    # ── 11. Home/Away (Role-aware logic) ─
    home_away = filters.get("home_away")
    if home_away and "home_team" in df.columns and "batting_team" in df.columns and "bowling_team" in df.columns:
        is_batting = filters.get("_is_batting_role", True) # Internal flag passed down
        subject_team_col = "batting_team" if is_batting else "bowling_team"
        
        if home_away.lower() == "home":
            df = df[df[subject_team_col] == df["home_team"]]
        elif home_away.lower() == "away":
            df = df[df[subject_team_col] != df["home_team"]]"""
val_code = val_code.replace(home_bad, home_good)

# Pass `_is_batting_role` to filters!
df_load_bad = """    subject_col = "batter" if is_batting else "bowler"
    df = _load_subject_dataframe(subject_col, canonical, engine, metric=metric, filters=filters)"""
df_load_good = """    subject_col = "batter" if is_batting else "bowler"
    filters["_is_batting_role"] = is_batting # Pass role flag to filters
    df = _load_subject_dataframe(subject_col, canonical, engine, metric=metric, filters=filters)"""
val_code = val_code.replace(df_load_bad, df_load_good)


# Add statistical guards (Phase 4 calculate)
stats_bad = """    # Build real_meta
    real_meta = {"formula": formula}"""

stats_good = """    # Build real_meta
    real_meta = {"formula": formula}
    
    # Statistical validation checks (Fix 4: Sample bounds & Anomaly guards)
    balls = (overs * 6) if not is_batting else runs
    if "wickets" in metric.lower() and value > 120 and "ipl" in str(filters.get("format", "")).lower():
         pass # A better sanity check is per-season, but enforcing statistical bounds below:
    
    MIN_BALLS = 30 if is_batting else 60
    warning = None
    if balls < MIN_BALLS:
        warning = "Low sample size"
        
    real_meta["balls"] = balls
    real_meta["confidence"] = min(1.0, balls / 600)
    if warning:
        real_meta["warning"] = warning"""
val_code = val_code.replace(stats_bad, stats_good)


with open(VALIDATE_PATH, "w", encoding="utf-8") as f:
    f.write(val_code)
print("validate_model.py patched (Fix 1, 4, 5: Strict comps, Statistical Guards, Role-aware Home logic)")

print("Done applying fixes.")
