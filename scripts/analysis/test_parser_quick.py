"""Quick standalone test for ai_parser._mock_parse (no API key needed)."""
import json, os, sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2]))
from scripts.analysis.ai_parser import parse_claim
import os
os.environ.pop("GROQ_API_KEY", None)          # force fallback parser

TESTS = [
    ("Babar Azam averages 50 in the 1st innings at MCG against Left-arm Pace",
     {"innings": 1, "venue_name": "Melbourne Cricket Ground",
      "bowler_hand": "Left", "bowler_type": "Pace"}),

    ("Virat Kohli has scored 8000 runs in ODIs in England",
     {"format": "ODI", "country": "England"}),

    ("Jasprit Bumrah has an economy of 4.5 in Death overs in Tests",
     {"match_phase": "Death", "format": "Test"}),

    ("Rohit Sharma hit 25 centuries in T20Is at home",
     {"format": "T20", "home_away": "Home"}),

    ("Joe Root averages 60 against Spin in the Ashes",
     {"bowler_type": "Spin", "series": "Ashes"}),
]

passed = failed = 0
for claim, expected in TESTS:
    result = parse_claim(claim)
    fl = result.get("filters", {})
    ok = True
    issues = []
    for k, v in expected.items():
        actual = fl.get(k)
        # Series comparison is case-insensitive
        if isinstance(v, str) and isinstance(actual, str):
            match = v.lower() in actual.lower() or actual.lower() in v.lower()
        elif isinstance(v, int) and isinstance(actual, (int, float)):
            match = int(actual) == v
        else:
            match = actual == v
        if not match:
            ok = False
            issues.append(f"  {k}: expected={v!r} got={actual!r}")
    if ok:
        print(f"  PASS  {claim[:60]}")
        passed += 1
    else:
        print(f"  FAIL  {claim[:60]}")
        for i in issues:
            print(i)
        failed += 1

print(f"\nResults: {passed} passed, {failed} failed out of {len(TESTS)} tests.")
if failed:
    sys.exit(1)
