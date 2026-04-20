import json
import os
import time


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


def main() -> int:
    # Avoid Windows console encoding issues for special characters.
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    from scripts.analysis.validate_model import validate_claim

    results: list[dict] = []
    suite_start = time.time()

    for i, q in enumerate(QUERIES, 1):
        q_start = time.time()
        try:
            r = validate_claim(q)
            results.append(
                {
                    "query": q,
                    "status": r.get("status"),
                    "subject": r.get("subject"),
                    "metric": r.get("metric"),
                    "real_val": r.get("real_val"),
                    "sample_size": r.get("sample_size"),
                    "confidence": r.get("confidence"),
                    "filters": r.get("filters"),
                    "message": r.get("message"),
                    "execution_mode": r.get("execution_mode"),
                    "verdict": r.get("verdict"),
                    "elapsed_s": round(time.time() - q_start, 3),
                }
            )
            print(f"[{i:02d}/{len(QUERIES)}] OK  - {q}")
        except Exception as e:
            results.append(
                {
                    "query": q,
                    "status": "error",
                    "message": str(e),
                    "elapsed_s": round(time.time() - q_start, 3),
                }
            )
            print(f"[{i:02d}/{len(QUERIES)}] ERR - {q} :: {e}")

    with open("user_query_report.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"Saved user_query_report.json ({len(results)} queries) in {round(time.time() - suite_start, 2)}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

