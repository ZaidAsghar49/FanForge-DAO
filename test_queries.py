import sys
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from scripts.analysis.validate_model import validate_claim

queries = [
    "Shaheen Afridi economy in powerplay overs in T20Is since 2021 in UAE",
    "Jasprit Bumrah performance in death overs in ODIs",
    "Rashid Khan wickets against left-handed batters in IPL",
    "James Anderson average in England in 2nd innings against right-hand batters after 2015",
    "Mitchell Starc effectiveness with the new ball in World Cups",
    "Trent Boult wickets in first 3 overs of innings in home matches",
    "Ali Khan economy in T20 leagues",
    "Pat Cummins strike rate in day-night Tests with pink ball",
    "Is Kagiso Rabada worse in Asia compared to outside Asia",
    "How good is Bhuvneshwar Kumar when defending totals at the death in IPL"
]

results = []
for idx, q in enumerate(queries):
    print(f"\n======================================")
    print(f"QUERY {idx+1}: {q}")
    print(f"======================================")
    try:
        res = validate_claim(q)
        results.append({
            "query": q,
            "status": res.get("status"),
            "subject": res.get("subject"),
            "metric": res.get("metric"),
            "real_val": res.get("real_val"),
            "message": res.get("message")
        })
    except Exception as e:
        print(f"ERROR: {e}")
        results.append({"query": q, "status": "code_error", "message": str(e)})

with open("test_queries_output.json", "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2)
