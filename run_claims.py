import subprocess
import sys
import os

# Force UTF-8 output
os.environ["PYTHONIOENCODING"] = "utf-8"

claims = [
    ("Role Reversal",         "Steve Smith has a bowling economy under 5.0 in Test matches."),
    ("Toss vs. Result",       "Virat Kohli averages 60 in matches where the team won the toss and batted first."),
    ("Boundary Reliance",     "Rohit Sharma's boundary percentage is over 70% in T20 Powerplays."),
    ("Specific Matchup",      "Rashid Khan has conceded fewer than 20 runs against Babar Azam in T20Is."),
    ("Dismissal Trend",       "Joe Root has been dismissed 'lbw' more than 30 times in his career."),
    ("Pressure Cooker",       "MS Dhoni has a strike rate of 150+ in the 20th over of ODI chases."),
    ("Venue Specialist",      "Kane Williamson averages 80 at the Bay Oval in 1st innings."),
    ("Partner Synergy",       "Babar Azam averages 50 when batting with Mohammad Rizwan."),
    ("Neutral Ground",        "Shaheen Afridi has taken 50 wickets in neutral venue ODIs."),
    ("Historical Range",      "Sachin Tendulkar scored 3000 runs between 2008 and 2011."),
]

for i, (name, claim) in enumerate(claims, 1):
    print(f"\n{'='*65}")
    print(f"  Test {i:02d}: {name}")
    print(f"  Claim: {claim}")
    print(f"{'='*65}")
    sys.stdout.flush()
    subprocess.run(["python", "scripts/analysis/validate_model.py", claim])
