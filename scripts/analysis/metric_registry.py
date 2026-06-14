"""
metric_registry.py — Deterministic Metric Formula Registry (with proofs)
=======================================================================
Single source of truth for metric definitions so computations are:
- consistent across the project
- auditable (returns formula + components)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd


@dataclass(frozen=True)
class MetricResult:
    status: str  # "ok" | "insufficient_data"
    value: float
    proof: dict


def _safe_div(n: float, d: float) -> float | None:
    if d == 0:
        return None
    return n / d


def batting_average(df: pd.DataFrame) -> MetricResult | None:
    if df.empty:
        return MetricResult(status="insufficient_data", value=0.0, proof={"sample_size": {"balls": 0}})
    runs = int(df["runs_batter"].sum()) if "runs_batter" in df.columns else 0
    dismissals = int(df["is_wicket"].sum()) if "is_wicket" in df.columns else 0
    innings = int(df["match_id"].nunique()) if "match_id" in df.columns else 1
    val = _safe_div(runs, dismissals)
    value = float(val) if val is not None else float(runs)
    return MetricResult(
        status="ok",
        value=value,
        proof={
            "formula": "Runs / Dismissals",
            "components": {"runs": runs, "dismissals": dismissals, "innings": innings},
            "sample_size": {"balls": int(len(df))},
        },
    )


def strike_rate(df: pd.DataFrame) -> MetricResult | None:
    if df.empty:
        return MetricResult(status="insufficient_data", value=0.0, proof={"sample_size": {"balls": 0}})
    runs = int(df["runs_batter"].sum()) if "runs_batter" in df.columns else 0
    # Cricket law: wides are NOT legal balls and do NOT count as balls faced.
    # extras_wides is number of wide runs, not a delivery count, so we count deliveries with wides.
    wide_deliveries = int((df["extras_wides"].fillna(0).astype(int) > 0).sum()) if "extras_wides" in df.columns else 0
    balls = int(len(df) - wide_deliveries)
    val = _safe_div(runs * 100.0, balls)
    value = float(val) if val is not None else 0.0
    return MetricResult(
        status="ok",
        value=value,
        proof={
            "formula": "(Runs / Balls) * 100",
            "components": {"runs": runs, "balls": balls, "wide_deliveries": wide_deliveries},
            "sample_size": {"balls": int(len(df))},
        },
    )


def total_runs(df: pd.DataFrame) -> MetricResult | None:
    if df.empty:
        return MetricResult(status="insufficient_data", value=0.0, proof={"sample_size": {"balls": 0}})
    runs = int(df["runs_batter"].sum()) if "runs_batter" in df.columns else 0
    return MetricResult(
        status="ok",
        value=float(runs),
        proof={"formula": "Sum(Runs)", "components": {"runs": runs}, "sample_size": {"balls": int(len(df))}},
    )


def wickets(df: pd.DataFrame) -> MetricResult | None:
    if df.empty:
        return MetricResult(status="insufficient_data", value=0.0, proof={"sample_size": {"balls": 0}})
    wkts = int(df["is_bowler_wicket"].sum()) if "is_bowler_wicket" in df.columns else 0
    return MetricResult(
        status="ok",
        value=float(wkts),
        proof={"formula": "Sum(Wickets)", "components": {"wickets": wkts}, "sample_size": {"balls": int(len(df))}},
    )


def economy_rate(df: pd.DataFrame) -> MetricResult | None:
    if df.empty:
        return MetricResult(status="insufficient_data", value=0.0, proof={"sample_size": {"balls": 0}})
    runs_total = int(df["runs_total"].sum()) if "runs_total" in df.columns else 0
    byes = int(df["extras_byes"].sum()) if "extras_byes" in df.columns else 0
    legbyes = int(df["extras_legbyes"].sum()) if "extras_legbyes" in df.columns else 0
    runs_conceded = runs_total - byes - legbyes
    # Cricket law: wides and no-balls are NOT legal deliveries, but their columns store RUNS, not counts.
    wide_deliveries = int((df["extras_wides"].fillna(0).astype(int) > 0).sum()) if "extras_wides" in df.columns else 0
    noball_deliveries = int((df["extras_noballs"].fillna(0).astype(int) > 0).sum()) if "extras_noballs" in df.columns else 0
    legal_deliveries = int(len(df) - wide_deliveries - noball_deliveries)
    overs = legal_deliveries / 6.0
    val = _safe_div(runs_conceded, overs)
    if val is None:
        return MetricResult(status="insufficient_data", value=0.0, proof={"sample_size": {"balls": int(len(df))}})
    return MetricResult(
        status="ok",
        value=float(val),
        proof={
            "formula": "Runs Conceded / Overs",
            "components": {
                "runs_total": runs_total,
                "byes": byes,
                "legbyes": legbyes,
                "runs_conceded": runs_conceded,
                "legal_deliveries": legal_deliveries,
                "overs": overs,
                "wide_deliveries": wide_deliveries,
                "noball_deliveries": noball_deliveries,
            },
            "sample_size": {"balls": int(len(df))},
        },
    )


def bowling_strike_rate(df: pd.DataFrame) -> MetricResult | None:
    if df.empty:
        return MetricResult(status="insufficient_data", value=0.0, proof={"sample_size": {"balls": 0}})
    wide_deliveries = int((df["extras_wides"].fillna(0).astype(int) > 0).sum()) if "extras_wides" in df.columns else 0
    noball_deliveries = int((df["extras_noballs"].fillna(0).astype(int) > 0).sum()) if "extras_noballs" in df.columns else 0
    legal_deliveries = int(len(df) - wide_deliveries - noball_deliveries)
    wkts = int(df["is_bowler_wicket"].sum()) if "is_bowler_wicket" in df.columns else 0
    val = _safe_div(legal_deliveries, wkts)
    value = float(val) if val is not None else 0.0
    return MetricResult(
        status="ok",
        value=value,
        proof={
            "formula": "Legal Deliveries / Wickets",
            "components": {"legal_deliveries": legal_deliveries, "wickets": wkts},
            "sample_size": {"balls": int(len(df))},
        },
    )


def bowling_average(df: pd.DataFrame) -> MetricResult | None:
    if df.empty:
        return MetricResult(status="insufficient_data", value=0.0, proof={"sample_size": {"balls": 0}})
    runs_total = int(df["runs_total"].sum()) if "runs_total" in df.columns else 0
    byes = int(df["extras_byes"].sum()) if "extras_byes" in df.columns else 0
    legbyes = int(df["extras_legbyes"].sum()) if "extras_legbyes" in df.columns else 0
    runs_conceded = runs_total - byes - legbyes
    wkts = int(df["is_bowler_wicket"].sum()) if "is_bowler_wicket" in df.columns else 0
    val = _safe_div(runs_conceded, wkts)
    value = float(val) if val is not None else float(runs_conceded)
    return MetricResult(
        status="ok",
        value=value,
        proof={
            "formula": "Runs Conceded / Wickets",
            "components": {"runs_conceded": runs_conceded, "wickets": wkts},
            "sample_size": {"balls": int(len(df))},
        },
    )


METRIC_REGISTRY: dict[str, Callable[[pd.DataFrame], MetricResult | None]] = {
    "Batting Average": batting_average,
    "Strike Rate": strike_rate,
    "Total Runs": total_runs,
    "Wickets": wickets,
    "Economy Rate": economy_rate,
    "Bowling Strike Rate": bowling_strike_rate,
    "Bowling Average": bowling_average,
}


def compute_metric(metric_name: str, df: pd.DataFrame) -> dict | None:
    fn = METRIC_REGISTRY.get(metric_name)
    if not fn:
        return None
    res = fn(df)
    if res is None:
        return None
    meta = {"formula": res.proof.get("formula"), **res.proof, "status": res.status}
    if res.status == "insufficient_data":
        return {"status": "insufficient_data", "value": None, "meta": meta}
    return {"status": "ok", "value": float(res.value), "meta": meta}

