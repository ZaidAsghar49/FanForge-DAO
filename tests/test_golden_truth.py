import json
from pathlib import Path

import pytest

from scripts.analysis.ai_parser import parse_claim


GOLDEN_FILE = Path(__file__).resolve().parent / "golden_queries.json"


def _load_cases():
    return json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _load_cases())
def test_golden_parser_contract(case):
    q = case["query"]
    exp = case["expect"]

    parsed = parse_claim(q)

    assert isinstance(parsed, dict)
    assert "filters" in parsed and isinstance(parsed["filters"], dict)

    if exp.get("subject_contains"):
        assert exp["subject_contains"].lower() in (parsed.get("subject") or "").lower()

    if exp.get("metric"):
        assert (parsed.get("metric") or "") == exp["metric"]

    exp_filters = exp.get("filters") or {}
    for k, v in exp_filters.items():
        assert parsed["filters"].get(k) == v

