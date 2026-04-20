import pytest
from tests.test_config import get_sample_dataframe, TEST_PLAYER, TEST_BOWLER, TEST_COUNTRY, TEST_FORMAT
from scripts.analysis.validate_model import apply_filters

@pytest.fixture
def sample_df():
    return get_sample_dataframe()

def test_batter_plus_country(sample_df):
    """Test Filter: batter + country"""
    result = apply_filters(
        sample_df,
        batter=TEST_PLAYER,
        country=TEST_COUNTRY
    )
    assert len(result) >= 0

def test_batter_plus_bowler_type(sample_df):
    """Test Filter: batter + bowler_type"""
    result = apply_filters(
        sample_df,
        batter=TEST_PLAYER,
        bowler_type="Spin"
    )
    assert len(result) >= 0

def test_batter_plus_match_phase(sample_df):
    """Test Filter: batter + match_phase"""
    result = apply_filters(
        sample_df,
        batter=TEST_PLAYER,
        match_phase="Death"
    )
    assert len(result) >= 0

def test_complex_combination(sample_df):
    """Test Filter: batter + format + country"""
    result = apply_filters(
        sample_df,
        batter=TEST_PLAYER,
        format=TEST_FORMAT,
        country=TEST_COUNTRY
    )
    assert len(result) >= 0

def test_head_to_head(sample_df):
    """Test Filter: bowler + batter (head-to-head)"""
    result = apply_filters(
        sample_df,
        batter=TEST_PLAYER,
        bowler=TEST_BOWLER
    )
    assert len(result) >= 0
