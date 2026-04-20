"""
run_queries_isolated.py
Runs each query in a fresh subprocess to prevent memory accumulation.
"""
import subprocess
import sys
import json
import os

QUERIES = [
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
sys.path.insert(0, 'D:/University/Semester 8th/FYP/AI')
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
print("CricketTruth AI — Isolated Query Runner")
print("=" * 65)

for i, q in enumerate(QUERIES, 1):
    print(f"\n[{i:02d}/{len(QUERIES)}] {q}")
    print("-" * 55)

    env = os.environ.copy()
    env["OPENBLAS_NUM_THREADS"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.run(
        [sys.executable, "-c", WORKER, q],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, env=env,
        cwd="D:/University/Semester 8th/FYP/AI"
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

    # Print key stdout lines (filtered)
    for line in stdout.splitlines():
        if any(kw in line for kw in ["Phase", ">>", "ACTUAL", "VERDICT", "SAMPLE", "FILTERS",
                                      "Resolved", "Plan type", "Post-filter", "Loaded",
                                      "Matched", "COMPARISON", "Asia", "Outside", "Delta",
                                      "Verdict", "WARN", "WARNING", "MODE"]):
            print(f"  {line.strip()}")

    s = result.get("status")
    v = result.get("real_val")
    ss = result.get("sample_size")
    conf = result.get("confidence")
    mode = result.get("execution_mode", "")
    verdict = result.get("verdict", "")

    if s == "ok":
        fmt_val = f"{v:.4f}" if v is not None else "N/A"
        fmt_ss  = f"{ss:,}" if ss else "?"
        fmt_cf  = f"{conf:.0%}" if conf is not None else "?"
        print(f"\n  STATUS  : OK | {result.get('metric')} = {fmt_val}")
        print(f"  VERDICT : {verdict}")
        print(f"  SAMPLE  : {fmt_ss} balls | CONFIDENCE: {fmt_cf} | MODE: {mode}")
        if result.get("filters"):
            print(f"  FILTERS : {result['filters']}")
    elif s == "no_data":
        print(f"\n  STATUS  : NO DATA — {result.get('message', '')}")
    else:
        print(f"\n  STATUS  : {s.upper()} — {result.get('message', '')}")

# ── Summary table ──────────────────────────────────────────────────────────
print("\n" + "=" * 65)
print("SUMMARY")
print("=" * 65)
print(f"{'#':<3} {'Query':<45} {'Val':>8} {'Conf':>6} {'Status'}")
print("-" * 65)
for i, r in enumerate(results, 1):
    q_short = r["query"][:44]
    v = r.get("real_val")
    c = r.get("confidence")
    st = r.get("status", "?")
    val_str  = f"{v:.4f}" if v is not None else "—"
    conf_str = f"{c:.0%}" if c is not None else "—"
    print(f"{i:<3} {q_short:<45} {val_str:>8} {conf_str:>6}  {st}")

# Save JSON
with open("test_queries_output.json", "w") as f:
    json.dump(results, f, indent=2)
print(f"\nResults saved to test_queries_output.json")
