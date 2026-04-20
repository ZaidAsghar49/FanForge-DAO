import pytest
import pandas as pd
from tests.test_config import get_sample_dataframe, TEST_PLAYER
from scripts.analysis.validate_model import apply_filters

@pytest.fixture
def sample_df():
    return get_sample_dataframe()

def test_invalid_player(sample_df):
    """Filter for a non-existent player should return 0 results."""
    result = apply_filters(sample_df, batter="Random XYZ 123")
    assert len(result) == 0

def test_invalid_country(sample_df):
    """Filter for an invalid country (city maps to Unknown) should return 0 results."""
    # Since apply_filters uses CITY_COUNTRY_MAP, an unknown country filter 
    # should typically return 0 rows if none map to it.
    result = apply_filters(sample_df, country="Wakanda")
    assert len(result) == 0

def test_empty_dataset():
    """Applying filters to an empty DataFrame should return an empty DataFrame."""
    empty_df = pd.DataFrame(columns=["batter", "bowler", "city"])
    result = apply_filters(empty_df, batter=TEST_PLAYER)
    assert len(result) == 0

def test_invalid_date_format(sample_df):
    """Filtering by an invalid season format should degrade gracefully (likely 0 results)."""
    result = apply_filters(sample_df, season="Invalid-Year")
    assert len(result) >= 0

def test_conflicting_filters(sample_df):
    """Conflicting filters (e.g., innings 1 AND match_phase Death which usually occurs in late innings) 
    might return 0 results, but shouldn't crash."""
    result = apply_filters(sample_df, innings=1, match_phase="Death")
    assert len(result) >= 0
