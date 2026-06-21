import pytest
import scripts.analysis.ai_parser

@pytest.fixture(autouse=True)
def mock_ai_parser(request, monkeypatch):
    # Do not mock for tests that explicitly verify the rule-based local parser logic offline
    if "test_over_filters" in request.node.nodeid:
        return

    def mock_parse_claim(claim_string):
        q = claim_string.lower()
        # Default empty/standard values
        subject = "Virat Kohli"
        metric = "Batting Average"
        claimed_value = None
        filters = {}

        if "babar" in q:
            subject = "Babar Azam"
            metric = "Batting Average"
            claimed_value = 50.0
            filters = {"country": "England", "innings": 2, "bowler_type": "Spin"}
        elif "bumrah" in q:
            subject = "Jasprit Bumrah"
            metric = "Economy Rate"
            claimed_value = None
            filters = {"format": "Test", "match_phase": "Death"}
            if "2020" in q:
                filters["season"] = "2020"
        elif "kohli" in q:
            subject = "Virat Kohli"
            metric = "Batting Average" if "average" in q else "Strike Rate"
            if "strike rate" in q:
                metric = "Strike Rate"
            claimed_value = 53.5 if "53.5" in q else None
            filters = {}
            if "india" in q:
                filters["country"] = "India"
            if "2023" in q:
                filters["as_of_date"] = "2023-12-31"
        elif "rashid" in q:
            subject = "Rashid Khan"
            metric = "Wickets"
            claimed_value = 100.0
            filters = {"format": "T20I"}

        return {
            "subject_type": "player",
            "subject": subject,
            "metric": metric,
            "claimed_value": claimed_value,
            "as_of_date": filters.get("as_of_date") or filters.get("season"),
            "filters": {
                "venue_name": None, "city": None, "country": filters.get("country"),
                "format": filters.get("format"), "season": filters.get("season"), "day_night": None,
                "toss_winner": None, "toss_decision": None,
                "innings": filters.get("innings"), "series": None, "home_away": None,
                "neutral_venue": None, "opposition": None,
                "dismissal_type": None, "batting_position": None,
                "non_striker": None, "milestones": None,
                "bowler": None, "bowler_type": filters.get("bowler_type"), "bowler_hand": None,
                "over_number": None, "over_range": None, "match_phase": filters.get("match_phase"),
                "batter_vs_bowler_type": None, "batter_vs_bowler": None, "as_of_date": filters.get("as_of_date")
            }
        }

    def mock_parse_paragraph(paragraph):
        return [mock_parse_claim(paragraph)]

    # Patch the root module functions
    monkeypatch.setattr(scripts.analysis.ai_parser, "parse_claim", mock_parse_claim)
    monkeypatch.setattr(scripts.analysis.ai_parser, "parse_paragraph", mock_parse_paragraph)

    # Patch locally bound references in test files to overcome module-level import binding issues
    try:
        monkeypatch.setattr("tests.test_golden_truth.parse_claim", mock_parse_claim)
    except AttributeError:
        pass
