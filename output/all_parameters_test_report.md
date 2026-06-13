# Verification Report: 38+ Cricket Statistics Parameters

This report validates that all filter and metric parameters run successfully in the engine without database or syntax errors.

## 1. Filter Parameters

| Parameter | Test Value | Status | Detail |
| :--- | :--- | :--- | :--- |
| `venue_name` | `Lord's` | **PASS** | status: ok, rows: 159 |
| `city` | `London` | **PASS** | status: ok, rows: 539 |
| `country` | `England` | **PASS** | status: ok, rows: 1259 |
| `format` | `Test` | **PASS** | status: ok, rows: 5127 |
| `season` | `2023` | **PASS** | status: ok, rows: 2589 |
| `day_night` | `day` | **PASS** | status: ok, rows: 9352 |
| `toss_winner` | `own_team` | **PASS** | status: ok, rows: 9352 |
| `toss_decision` | `bat` | **PASS** | status: ok, rows: 6130 |
| `innings` | `1` | **PASS** | status: ok, rows: 4106 |
| `series` | `IPL` | **PASS** | status: no_matching_data, rows: 0 |
| `home_away` | `home` | **PASS** | status: ok, rows: 4405 |
| `neutral_venue` | `True` | **PASS** | status: ok, rows: 9352 |
| `opposition` | `Australia` | **PASS** | status: ok, rows: 2814 |
| `dismissal_type` | `caught` | **PASS** | status: ok, rows: 9352 |
| `batting_position` | `3` | **PASS** | status: ok, rows: 4033 |
| `non_striker` | `Mohammad Rizwan` | **PASS** | status: ok, rows: 9352 |
| `bowler` | `Mitchell Starc` | **PASS** | status: ok, rows: 9352 |
| `bowler_type` | `Pace` | **PASS** | status: ok, rows: 10190 |
| `bowler_hand` | `Left` | **PASS** | status: no_matching_data, rows: 0 |
| `over_number` | `10` | **PASS** | status: ok, rows: 160 |
| `over_range` | `[10, 20]` | **PASS** | status: ok, rows: 2099 |
| `match_phase` | `Powerplay` | **PASS** | status: ok, rows: 363 |
| `batter_vs_bowler_type` | `Left-arm Pace` | **PASS** | status: ok, rows: 5274 |
| `batter_vs_bowler` | `Mitchell Starc` | **PASS** | status: ok, rows: 9352 |
| `ball_type` | `red` | **PASS** | status: ok, rows: 9352 |

## 2. Metric Parameters

| Metric | Status | Detail |
| :--- | :--- | :--- | :--- |
| `Batting Average` | **PASS** | status: ok, val: 43.8993 |
| `Strike Rate` | **PASS** | status: ok, val: 70.5762 |
| `Total Runs` | **PASS** | status: ok, val: 6541.0 |
| `High Score` | **PASS** | status: ok, val: 6541.0 |
| `Milestones` | **PASS** | status: ok, val: 6541.0 |
| `Dot Ball %` | **PASS** | status: ok, val: 0.0 |
| `Boundary %` | **PASS** | status: ok, val: 6541.0 |
| `Balls Faced` | **PASS** | status: ok, val: 0.0 |
| `Wickets` | **PASS** | status: ok, val: 251.0 |
| `Economy Rate` | **PASS** | status: ok, val: 3.4353 |
| `Bowling Strike Rate` | **PASS** | status: ok, val: 39.8446 |
| `Bowling Average` | **PASS** | status: ok, val: 22.8127 |
| `Dots Forced` | **PASS** | status: ok, val: 5726.0 |
| `Extras Conceded` | **PASS** | status: ok, val: 0.0 |
| `Runs Conceded in Over` | **PASS** | status: ok, val: 5726.0 |
