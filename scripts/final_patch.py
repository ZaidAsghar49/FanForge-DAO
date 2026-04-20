import sys
import os

with open('scripts/analysis/validate_model.py', 'r', encoding='utf-8') as f:
    code = f.read()

# Fix _get_required_columns
bad_req = """    for key, val in filters.items():
        if val is not None:
            if key == "venue_name": cols.add("venue_name")
            elif key == "city": cols.add("city")
            elif key == "country": cols.add("city")
            elif key == "format": cols.add("match_type")"""
good_req = """    for key, val in filters.items():
        if val is not None:
            if key == "venue_name": cols.add("venue_name")
            elif key == "city": cols.add("city")
            elif key == "country": cols.add("city"); cols.add("country")
            elif key == "format": cols.add("match_type"); cols.add("competition")"""
code = code.replace(bad_req, good_req)

# Fix apply_filters country
bad_cntry = """    # ── 3. Country ────────────────────────────────────────────────────────────
    country = filters.get("country")
    if country and "city" in df.columns:
        # Vectorized country lookup
        df = df[df["city"].map(CITY_COUNTRY_MAP).fillna("Unknown").str.lower() == country.lower()]"""
good_cntry = """    # ── 3. Country ────────────────────────────────────────────────────────────
    country = filters.get("country")
    if country:
        if "country" in df.columns:
            df = df[df["country"].str.lower() == country.lower()]
        elif "city" in df.columns:
            df = df[df["city"].map(CITY_COUNTRY_MAP).fillna("Unknown").str.lower() == country.lower()]"""
code = code.replace(bad_cntry, good_cntry)

# Fix T20I / ODI aliasing
bad_fmt = """        if "ipl" in fmt.lower() and "competition" in df.columns:
            df = df[df["competition"].str.contains("IPL|Indian Premier League", case=False, na=False)]
        elif fmt.lower() == "international":"""
good_fmt = """        if "ipl" in fmt.lower() and "competition" in df.columns:
            df = df[df["competition"].str.contains("IPL|Indian Premier League", case=False, na=False)]
        elif fmt.lower() == "t20i":
            df = df[df["match_type"].str.contains("IT20", case=False, na=False)]
        elif fmt.lower() == "odi":
            df = df[df["match_type"].str.contains("ODI|ODM", case=False, na=False)]
        elif fmt.lower() == "international":"""
code = code.replace(bad_fmt, good_fmt)

with open('scripts/analysis/validate_model.py', 'w', encoding='utf-8') as f:
    f.write(code)

print("final patch logic applied!")
