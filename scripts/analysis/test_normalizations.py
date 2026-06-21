import json
from ai_parser import FiltersModel

def test_pydantic_normalizations():
    # 1. venue_name
    f1 = FiltersModel(venue_name="beautiful pitch at The G")
    assert f1.venue_name == "The G", f1.venue_name
    
    # 2. country
    f2 = FiltersModel(country="UK")
    assert f2.country == "England", f2.country
    
    # 3. format
    f3 = FiltersModel(format="five-day matches")
    assert f3.format == "Test", f3.format
    
    # 4. season
    f4 = FiltersModel(season="2022/23")
    assert f4.season == "2022", f4.season
    
    # 5. day_night
    f5 = FiltersModel(day_night="under lights")
    assert f5.day_night == "Day-Night", f5.day_night
    
    # 6. toss_decision
    f6 = FiltersModel(toss_decision="chose to bat first")
    assert f6.toss_decision == "bat", f6.toss_decision
    
    # 7. innings
    f7 = FiltersModel(innings="second innings")
    assert f7.innings == 2, f7.innings
    
    # 8. home_away
    f8 = FiltersModel(home_away="touring")
    assert f8.home_away == "Away", f8.home_away
    
    print("All Pydantic schema coercion tests passed!")

if __name__ == "__main__":
    test_pydantic_normalizations()
