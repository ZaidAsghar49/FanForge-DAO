import pandas as pd
from metric_registry import compute_metric, _filter_super_overs

def test_metrics():
    # Mock Dataframe for Testing
    df = pd.DataFrame({
        "match_id": [1, 1, 1, 1, 2, 2, 2, 2, 3],
        "match_type": ["ODI", "ODI", "ODI", "ODI", "T20", "T20", "T20", "T20", "Test"],
        "innings": [1, 1, 3, 4, 1, 1, 1, 3, 4], # match 1 has super over (inns 3, 4), match 2 has super over (inns 3), match 3 is test with inns 4
        "runs_batter": [4, 0, 6, 1, 6, 4, 0, 4, 100],
        "extras_wides": [0, 1, 0, 0, 0, 0, 0, 0, 0],
        "extras_noballs": [0, 0, 0, 0, 0, 0, 1, 0, 0], # no ball, 0 runs scored off bat -> dot ball
        "runs_total": [4, 1, 6, 1, 6, 4, 1, 4, 100],
        "is_wicket": [0, 0, 0, 1, 0, 0, 0, 1, 0]
    })
    
    # 1. Test Super Over Exclusions
    # For ODI: inns 3 and 4 are excluded. For T20: inns 3 is excluded. For Test: inns 4 is kept.
    # Total valid rows: ODI inns 1 (2 rows), T20 inns 1 (3 rows), Test inns 4 (1 row) -> Total 6 rows.
    df_filtered = _filter_super_overs(df)
    assert len(df_filtered) == 6, f"Expected 6 rows after super over filter, got {len(df_filtered)}"
    
    # Let's test the metrics over the filtered dataframe
    
    # Total Runs: ODI (4+0), T20 (6+4+0), Test (100) -> 114
    runs_res = compute_metric("Total Runs", df)
    assert runs_res["value"] == 114, f"Total Runs failed: {runs_res['value']}"
    assert isinstance(runs_res["value"], int), "Total Runs should be an integer"
    
    # Balls Faced: ODI (2 rows - 1 wide = 1 ball), T20 (3 rows - 0 wides = 3 balls), Test (1 row = 1 ball) -> 5 balls
    balls_res = compute_metric("Balls Faced", df)
    assert balls_res["value"] == 5, f"Balls Faced failed: {balls_res['value']}"
    assert isinstance(balls_res["value"], int), "Balls Faced should be an integer"
    
    # Strike Rate: (114 / 5) * 100 = 2280.0
    sr_res = compute_metric("Strike Rate", df)
    assert sr_res["value"] == 2280.0, f"Strike Rate failed: {sr_res['value']}"
    
    # Dot Ball %: 
    # ODI: wide is excluded. No dot balls.
    # T20: 0 runs off no-ball -> 1 dot ball.
    # Test: 100 runs -> 0 dot balls.
    # Total dots = 1. Total legal balls = 5. (1/5) * 100 = 20.0%
    dot_res = compute_metric("Dot Ball %", df)
    assert dot_res["value"] == 20.0, f"Dot Ball % failed: {dot_res['value']}"
    
    # Boundary %:
    # Fours: ODI (1), T20 (1) -> 2 fours (8 runs)
    # Sixes: T20 (1) -> 1 six (6 runs)
    # Total boundary runs: 14. Total bat runs: 114. (14 / 114) * 100 = 12.28... %
    bound_res = compute_metric("Boundary %", df)
    assert abs(bound_res["value"] - 12.2807) < 0.001, f"Boundary % failed: {bound_res['value']}"
    
    # Batting Average:
    # Total valid dismissals: 0. (Wait, ODI innings 4 wicket is super over so it's dropped. T20 innings 3 wicket is dropped.)
    # Total valid runs: 114. 
    # Since dismissals=0, batting average should return proxy runs (114.0)
    avg_res = compute_metric("Batting Average", df)
    assert avg_res["value"] == 114.0, f"Batting Average proxy failed: {avg_res['value']}"
    
    # High Score:
    # Match 1 Inns 1: 4 runs (not out)
    # Match 2 Inns 1: 10 runs (not out)
    # Match 3 Inns 4: 100 runs (not out)
    # Max is 100. is_not_out = True
    hs_res = compute_metric("High Score", df)
    assert hs_res["value"] == 100, f"High Score failed: {hs_res['value']}"
    assert hs_res["meta"]["components"]["is_not_out"] is True, "High Score is_not_out flag failed"
    
    # Partnership Runs: Sum(runs_total) = 4+1 + 6+4+1 + 100 = 116
    pr_res = compute_metric("Partnership Runs", df)
    assert pr_res["value"] == 116, f"Partnership Runs failed: {pr_res['value']}"
    assert isinstance(pr_res["value"], int), "Partnership Runs should be an integer"

    print("All Phase 2 metric tests passed successfully!")

if __name__ == "__main__":
    test_metrics()
