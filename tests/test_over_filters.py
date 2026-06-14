import os

import pandas as pd

from scripts.analysis.ai_parser import parse_claim
from scripts.analysis.validate_model import apply_filters


def test_parser_between_overs_extracts_over_range():
    os.environ.pop("GROQ_API_KEY", None)  # force rule-based path for determinism
    parsed = parse_claim("Babar Azam strike rate between overs 3 and 4 in T20")
    assert parsed["filters"]["over_range"] == [2, 3]  # 0-indexed internally


def test_apply_filters_over_range_filters_rows():
    df = pd.DataFrame({"over": list(range(6)), "runs_total": list(range(6))})
    out = apply_filters(df, {"over_range": [2, 3]}, engine=None, is_batting=True)
    assert out["over"].tolist() == [2, 3]


def test_apply_filters_over_number_filters_row():
    df = pd.DataFrame({"over": list(range(6)), "runs_total": list(range(6))})
    out = apply_filters(df, {"over_number": 4}, engine=None, is_batting=True)
    assert out["over"].tolist() == [4]

