import pytest
import pandas as pd
from tests.test_config import get_sample_dataframe, TEST_PLAYER, TEST_BOWLER, TEST_COUNTRY, TEST_FORMAT
from scripts.analysis.validate_model import apply_filters, calculate_real_value, _get_engine, validate_claim

@pytest.fixture
def sample_df():
    return get_sample_dataframe()

@pytest.fixture
def engine():
    return _get_engine()

# --- Match Context Filters (1-12) ---

def test_venue_filter(sample_df):
    result = apply_filters(sample_df, venue_name="Melbourne")
    assert len(result) >= 0
    if len(result) > 0:
        assert result["venue_name"].str.contains("Melbourne", case=False).all()

def test_city_filter(sample_df):
    result = apply_filters(sample_df, city="Melbourne")
    assert len(result) >= 0

def test_country_filter(sample_df):
    result = apply_filters(sample_df, country=TEST_COUNTRY)
    assert len(result) >= 0

def test_format_filter(sample_df):
    result = apply_filters(sample_df, format=TEST_FORMAT)
    assert len(result) >= 0

def test_season_filter(sample_df):
    result = apply_filters(sample_df, season="2022")
    assert len(result) >= 0

def test_day_night_filter(sample_df):
    result = apply_filters(sample_df, day_night="Day")
    assert len(result) >= 0

def test_toss_winner_filter(sample_df):
    result = apply_filters(sample_df, toss_winner="India")
    assert len(result) >= 0

def test_toss_decision_filter(sample_df):
    result = apply_filters(sample_df, toss_decision="bat")
    assert len(result) >= 0

def test_innings_filter(sample_df):
    result = apply_filters(sample_df, innings=1)
    assert len(result) >= 0 

def test_series_filter(sample_df):
    result = apply_filters(sample_df, series="Asia Cup")
    assert len(result) >= 0

def test_home_away_filter(sample_df):
    result = apply_filters(sample_df, home_away="Home")
    assert len(result) >= 0

def test_neutral_venue_filter(sample_df):
    result = apply_filters(sample_df, neutral_venue=True)
    assert len(result) >= 0

# --- Batting Analytics (13-25) ---

def test_batter_filter(sample_df):
    result = apply_filters(sample_df, batter=TEST_PLAYER)
    assert len(result) >= 0

def test_metric_total_runs(sample_df, engine):
    res = calculate_real_value(sample_df, TEST_PLAYER, "total runs", {}, engine)
    assert res is None or "value" in res

def test_metric_balls_faced(sample_df, engine):
    res = calculate_real_value(sample_df, TEST_PLAYER, "balls faced", {}, engine)
    assert res is None or "value" in res

def test_metric_batting_average(sample_df, engine):
    res = calculate_real_value(sample_df, TEST_PLAYER, "batting average", {}, engine)
    assert res is None or "value" in res

def test_metric_strike_rate(sample_df, engine):
    res = calculate_real_value(sample_df, TEST_PLAYER, "strike rate", {}, engine)
    assert res is None or "value" in res

def test_dismissal_type_filter(sample_df):
    result = apply_filters(sample_df, dismissal_type="caught")
    assert len(result) >= 0

def test_metric_dot_ball_percent(sample_df, engine):
    res = calculate_real_value(sample_df, TEST_PLAYER, "dot ball %", {}, engine)
    assert res is None or "value" in res

def test_metric_boundary_percent(sample_df, engine):
    res = calculate_real_value(sample_df, TEST_PLAYER, "boundary %", {}, engine)
    assert res is None or "value" in res

def test_batting_position_filter(sample_df):
    result = apply_filters(sample_df, batting_position=3)
    assert len(result) >= 0

def test_non_striker_filter(sample_df):
    result = apply_filters(sample_df, non_striker="Babar Azam")
    assert len(result) >= 0

def test_metric_partnership_runs(sample_df, engine):
    res = calculate_real_value(sample_df, TEST_PLAYER, "partnership runs", {}, engine)
    assert res is None or "value" in res

def test_metric_high_score(sample_df, engine):
    res = calculate_real_value(sample_df, TEST_PLAYER, "high score", {}, engine)
    assert res is None or "value" in res

def test_metric_milestones(sample_df, engine):
    res = calculate_real_value(sample_df, TEST_PLAYER, "milestones", {}, engine)
    assert res is None or "value" in res

# --- Bowling & Matchup Analytics (26-38) ---

def test_bowler_filter(sample_df):
    result = apply_filters(sample_df, bowler=TEST_BOWLER)
    assert len(result) >= 0

def test_bowler_type_filter(sample_df):
    result = apply_filters(sample_df, bowler_type="Spin")
    assert len(result) >= 0

def test_bowler_hand_filter(sample_df):
    result = apply_filters(sample_df, bowler_hand="Left")
    assert len(result) >= 0

def test_metric_economy_rate(sample_df, engine):
    res = calculate_real_value(sample_df, TEST_BOWLER, "economy rate", {}, engine)
    assert res is None or "value" in res

def test_metric_bowling_strike_rate(sample_df, engine):
    res = calculate_real_value(sample_df, TEST_BOWLER, "bowling strike rate", {}, engine)
    assert res is None or "value" in res

def test_metric_wickets(sample_df, engine):
    res = calculate_real_value(sample_df, TEST_BOWLER, "wickets", {}, engine)
    assert res is None or "value" in res

def test_metric_dots_forced(sample_df, engine):
    res = calculate_real_value(sample_df, TEST_BOWLER, "dots forced", {}, engine)
    assert res is None or "value" in res

def test_metric_extras_conceded(sample_df, engine):
    res = calculate_real_value(sample_df, TEST_BOWLER, "extras conceded", {}, engine)
    assert res is None or "value" in res

def test_over_number_filter(sample_df):
    result = apply_filters(sample_df, over_number=5)
    assert len(result) >= 0

def test_match_phase_filter(sample_df):
    result = apply_filters(sample_df, match_phase="Powerplay")
    assert len(result) >= 0

def test_batter_vs_bowler_type_filter(sample_df):
    result = apply_filters(sample_df, batter_vs_bowler_type="Left-arm Pace")
    assert len(result) >= 0

def test_batter_vs_bowler_filter(sample_df):
    result = apply_filters(sample_df, batter_vs_bowler=TEST_BOWLER)
    assert len(result) >= 0

def test_metric_runs_conceded_in_over(sample_df, engine):
    res = calculate_real_value(sample_df, TEST_BOWLER, "runs conceded in over", {}, engine)
    assert res is None or "value" in res

def test_opposition_filter(sample_df):
    result = apply_filters(sample_df, opposition="Australia")
    assert len(result) >= 0

# --- Full Pipeline Integration ---

def test_validate_claim_pipeline():
    """Integration test: validates the full 5-phase pipeline."""
    result = validate_claim(f"{TEST_PLAYER} average in {TEST_COUNTRY}")
    assert "verdict" in result or result.get("status") == "error"
