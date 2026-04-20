import subprocess
import sys
import json
import os

USER_QUERIES = [
    "Jasprit Bumrah economy in ODIs",
    "Shaheen Afridi wickets in T20Is",
    "Kagiso Rabada bowling average in Test matches",
    "Rashid Khan economy in IPL",
    "Trent Boult wickets in ODIs at home",
    "Mitchell Starc strike rate in World Cups",
    "Bhuvneshwar Kumar economy in powerplay overs in IPL",
    "James Anderson wickets in England Test matches after 2015",
    "Pat Cummins economy in day night Test matches",
    "Sunil Narine wickets in IPL middle overs",
]

WORKER = """
import os, sys, gc
os.environ['OPENBLAS_NUM_THREADS'] = '1'
sys.path.insert(0, 'd:/University/Semester 8th/FYP/AI')
import json

query = sys.argv[1]
try:
    from scripts.analysis.validate_model import validate_claim
    r = validate_claim(query)
    out = {
        "query":    query,
        "status":   r.get("status"),
        "subject":  r.get("subject"),
        "metric":   r.get("metric"),
        "real_val": r.get("real_val"),
        "sample_size": r.get("sample_size"),
        "confidence":  r.get("confidence"),
        "execution_mode": r.get("execution_mode"),
        "verdict":  r.get("verdict"),
        "filters":  r.get("filters"),
        # comparison
        "Asia":         r.get("Asia"),
        "Outside Asia": r.get("Outside Asia"),
        "delta":        r.get("delta"),
        "message":  r.get("message"),
    }
except Exception as e:
    out = {"query": query, "status": "error", "message": str(e)}

print("RESULT_JSON:" + json.dumps(out))
"""

results = []

print("=" * 65)
print("CricketTruth AI — User Query Test Suite")
print("=" * 65)

for i, q in enumerate(USER_QUERIES, 1):
    print(f"\n[{i:02d}/{len(USER_QUERIES)}] {q}")
    print("-" * 55)

    env = os.environ.copy()
    env["OPENBLAS_NUM_THREADS"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.run(
        [sys.executable, "-c", WORKER, q],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, env=env,
        cwd="d:/University/Semester 8th/FYP/AI"
    )

    stdout = proc.stdout or ""
    result = None
    for line in stdout.splitlines():
        if line.startswith("RESULT_JSON:"):
            try:
                result = json.loads(line[len("RESULT_JSON:"):])
            except Exception:
                pass

    if result is None:
        result = {"query": q, "status": "error", "message": "subprocess failed or no output"}
        if proc.returncode != 0 and proc.stderr:
            snippet = (proc.stderr or "").strip().splitlines()[-1]
            result["message"] = snippet

    results.append(result)

    # Print summary status
    s = result.get("status")
    v = result.get("real_val")
    metric = result.get("metric")
    
    if s == "ok":
        fmt_val = f"{v:.4f}" if v is not None else "N/A"
        print(f"  STATUS  : OK | {metric} = {fmt_val}")
    elif s == "no_data":
         print(f"  STATUS  : NO DATA — {result.get('message', '')}")
    else:
         print(f"  STATUS  : {str(s).upper()} — {result.get('message', '')}")

# Save Report
with open("user_query_report.json", "w") as f:
    json.dump(results, f, indent=2)

print(f"\nReport saved to user_query_report.json")
