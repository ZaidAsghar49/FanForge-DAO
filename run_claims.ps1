$claims = @(
    "Steven Smith averages 60 at the Adelaide Oval in the 2nd innings.",
    "Virat Kohli has been dismissed 'lbw' more than 20 times in ODIs.",
    "Babar Azam averages under 30 against Left-arm Pace in Test matches.",
    "Joe Root has scored more than 5000 runs in away Test matches.",
    "Rashid Khan's economy is exactly 6.0 during the Powerplay in T20s.",
    "Kane Williamson averages 55 in matches where his team lost the toss.",
    "Rohit Sharma averages 50 when batting with Shikhar Dhawan.",
    "MS Dhoni's strike rate is over 200 in the last 2 overs of ODI innings.",
    "David Warner has scored 1000 runs in neutral venue T20 internationals.",
    "Sachin Tendulkar scored 2000 runs between the years 2010 and 2012."
)

for ($i = 0; $i -lt $claims.Length; $i++) {
    Write-Host "`n================== Test $($i + 1) =================="
    Write-Host "Claim: $($claims[$i])"
    python scripts/analysis/validate_model.py $claims[$i]
}
