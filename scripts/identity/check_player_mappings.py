"""
check_player_mappings.py  (fast version — direct lookup only, no fuzzy)
==========================================================================
Checks every player in cricketers.csv against the player DB
(players_data_with_all_info.csv) using exact-name lookup only.

Categories:
  MAPPED      – name resolves to exactly ONE player (confident)
  AMBIGUOUS   – last-name or initial variant matches > 1 player
  UNMAPPED    – no match in the DB at all

Saves:
  mapped_players.csv
  ambiguous_players.csv
  unmapped_players.csv
"""

import pandas as pd
import re
import os
import sys
from collections import defaultdict

from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────────────
ROOT           = Path(__file__).resolve().parents[2]
PLAYERS_DB     = str(ROOT / 'Dataset' / 'Players' / 'players_data_with_all_info.csv')
CRICKETERS_CSV = str(ROOT / 'Dataset' / 'Players' / 'cricketers.csv')
OUTPUT_DIR     = ROOT / 'output'

# ── Helpers ──────────────────────────────────────────────────────────────────
def build_lookup(df):
    """
    Build a lookup_map: lowercase_variant -> list of row-dicts from players_data.
    Variants added per player:
      1. full name           e.g. "virat kohli"
      2. initial + last      e.g. "v kohli"
      3. last name only      e.g. "kohli"
      4. full name no spaces e.g. "viratkohli"
    """
    lookup_map = defaultdict(list)
    for _, row in df.iterrows():
        pid   = str(row['id'])
        fname = str(row.get('fullname', '')).strip()
        first = str(row.get('firstname', '')).strip()
        last  = str(row.get('lastname', '')).strip()
        if not fname or fname == 'nan':
            continue

        rec = {
            'player_id':       pid,
            'canonical_name':  fname,
            'country':         row.get('country_name', ''),
            'position':        row.get('position', ''),
        }

        variants = set()
        variants.add(fname.lower())
        variants.add(fname.replace(' ', '').lower())
        if first and last:
            variants.add(f"{first[0]} {last}".lower())
            variants.add(f"{first[0]}.{last}".lower())
        if last:
            variants.add(last.lower())

        for v in variants:
            lookup_map[v].append(rec)

    return lookup_map


def resolve_name(name, lookup_map):
    """
    Try to find `name` in the lookup_map.
    Returns  (status, candidates)
      status: 'mapped' | 'ambiguous' | 'unmapped'
    """
    tokens = re.split(r'\W+', name.strip().lower())
    n = len(tokens)

    # Sliding window from longest to shortest
    for window_len in range(min(4, n), 0, -1):
        for i in range(n - window_len + 1):
            segment = ' '.join(tokens[i: i + window_len])
            if segment in lookup_map:
                hits = lookup_map[segment]
                if len(hits) == 1:
                    return 'mapped', hits
                else:
                    return 'ambiguous', hits

    return 'unmapped', []


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    os.chdir(str(ROOT))

    print(f"Loading player DB: {PLAYERS_DB} ...")
    db_df = pd.read_csv(PLAYERS_DB)
    print(f"  {len(db_df)} players in DB.")

    print("Building lookup map ...")
    lookup_map = build_lookup(db_df)
    print(f"  {len(lookup_map)} name variants indexed.")

    print(f"\nLoading cricketers.csv ...")
    cricketers = pd.read_csv(CRICKETERS_CSV)
    print(f"  {len(cricketers)} players to check.\n")

    mapped_rows    = []
    ambiguous_rows = []
    unmapped_rows  = []

    for _, row in cricketers.iterrows():
        name    = str(row.get('Name', '')).strip()
        country = str(row.get('Country', '')).strip()
        if not name or name == 'nan':
            continue

        status, candidates = resolve_name(name, lookup_map)

        if status == 'mapped':
            c = candidates[0]
            mapped_rows.append({
                'cricket_name':    name,
                'cricket_country': country,
                'db_id':           c['player_id'],
                'db_name':         c['canonical_name'],
                'db_country':      c['country'],
                'db_position':     c['position'],
            })

        elif status == 'ambiguous':
            cand_names    = [c['canonical_name'] for c in candidates]
            cand_ids      = [c['player_id']      for c in candidates]
            cand_countries= [c['country']        for c in candidates]
            ambiguous_rows.append({
                'cricket_name':       name,
                'cricket_country':    country,
                'num_candidates':     len(candidates),
                'candidate_names':    ' | '.join(cand_names[:8]),
                'candidate_ids':      ' | '.join(cand_ids[:8]),
                'candidate_countries':' | '.join(cand_countries[:8]),
            })

        else:  # unmapped
            unmapped_rows.append({
                'cricket_name':    name,
                'cricket_country': country,
            })

    total   = len(mapped_rows) + len(ambiguous_rows) + len(unmapped_rows)
    mp, am, um = len(mapped_rows), len(ambiguous_rows), len(unmapped_rows)

    print("=" * 70)
    print("  PLAYER MAPPING REPORT (Direct Lookup)")
    print("=" * 70)
    print(f"  Total checked  : {total}")
    print(f"  ✅ MAPPED      : {mp:>5}  ({mp/total*100:5.1f}%)")
    print(f"  ⚠️  AMBIGUOUS   : {am:>5}  ({am/total*100:5.1f}%)")
    print(f"  ❌ UNMAPPED    : {um:>5}  ({um/total*100:5.1f}%)")
    print("=" * 70)

    # ── Ambiguous Detail ───────────────────────────────────────────────────
    print(f"\n{'─'*70}")
    print(f"AMBIGUOUS PLAYERS  ({am} total)")
    print(f"{'─'*70}")
    for r in ambiguous_rows:
        cnames = r['candidate_names']
        print(f"  {r['cricket_name']:<36} [{r['cricket_country']}]")
        print(f"    {r['num_candidates']} candidates: {cnames}")

    # ── Unmapped Detail ────────────────────────────────────────────────────
    print(f"\n{'─'*70}")
    print(f"UNMAPPED PLAYERS  ({um} total)  — showing first 150")
    print(f"{'─'*70}")
    for r in unmapped_rows[:150]:
        print(f"  {r['cricket_name']:<36} [{r['cricket_country']}]")
    if um > 150:
        print(f"  ... and {um - 150} more (see unmapped_players.csv)")

    # ── Save CSVs ─────────────────────────────────────────────────────────
    OUTPUT_DIR.mkdir(exist_ok=True)
    pd.DataFrame(mapped_rows).to_csv(OUTPUT_DIR / 'mapped_players.csv', index=False)
    pd.DataFrame(ambiguous_rows).to_csv(OUTPUT_DIR / 'ambiguous_players.csv', index=False)
    pd.DataFrame(unmapped_rows).to_csv(OUTPUT_DIR / 'unmapped_players.csv', index=False)
    print(f"\nSaved:")
    print(f"  output/mapped_players.csv       ({mp} rows)")
    print(f"  output/ambiguous_players.csv    ({am} rows)")
    print(f"  output/unmapped_players.csv     ({um} rows)")


if __name__ == '__main__':
    main()
