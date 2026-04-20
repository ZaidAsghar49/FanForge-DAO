import sys

with open('scripts/analysis/validate_model.py', 'r', encoding='utf-8') as f:
    code = f.read()

# Fix season
bad_season = """        else:
            year = season_str[:4]
            df = df[df["date"].astype(str).str.startswith(year)]"""

good_season = """        elif "after" in season_str or "onwards" in season_str or "since" in season_str:
            match = _re.search(r"(\\d{4})", season_str)
            if match:
                yr_start = int(match.group(1))
                df = df[df["date"].astype(str).str[:4].astype(int) >= yr_start]
        elif "before" in season_str or "until" in season_str:
            match = _re.search(r"(\\d{4})", season_str)
            if match:
                yr_end = int(match.group(1))
                df = df[df["date"].astype(str).str[:4].astype(int) <= yr_end]
        else:
            match = _re.search(r"(\\d{4})", season_str)
            if match:
                year = match.group(1)
                df = df[df["date"].astype(str).str.startswith(year)]"""
code = code.replace(bad_season, good_season)

# Fix format for IPL
bad_fmt = """    fmt = filters.get("format")
    if fmt and "match_type" in df.columns:
        if fmt.lower() == "international":"""

good_fmt = """    fmt = filters.get("format")
    if fmt and "match_type" in df.columns:
        if "ipl" in fmt.lower() and "competition" in df.columns:
            df = df[df["competition"].str.contains("IPL|Indian Premier League", case=False, na=False)]
        elif fmt.lower() == "international":"""
code = code.replace(bad_fmt, good_fmt)

with open('scripts/analysis/validate_model.py', 'w', encoding='utf-8') as f:
    f.write(code)

print('Patched validate_model.py')
