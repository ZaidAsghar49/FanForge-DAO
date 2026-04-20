import duckdb
con = duckdb.connect('data/processed/cricket.duckdb', read_only=True)

# Q1: Shaheen UAE match_types
print("=== Q1: Shaheen UAE match_types ===")
r = con.execute("SELECT match_type, COUNT(*) FROM deliveries WHERE bowler LIKE '%Shah Afridi%' AND lower(country)=lower('UAE') GROUP BY match_type").fetchall()
print(r)
r2 = con.execute("SELECT COUNT(*) FROM deliveries WHERE match_type IN ('IT20','T20I')").fetchone()
print("IT20/T20I total rows:", r2)
all_types = con.execute("SELECT DISTINCT match_type FROM deliveries").fetchall()
print("All match types:", all_types)

# Q3: Rashid per season
print()
r4 = con.execute("SELECT SUBSTR(date,1,4) as yr, SUM(is_bowler_wicket) as wkts, ROUND(COUNT(*)/6.0,1) as overs FROM deliveries WHERE bowler LIKE '%Rashid%' AND competition='Indian Premier League' GROUP BY yr ORDER BY yr").fetchall()
print("Rashid IPL by year:", r4)

# Q6: Boult home (corrected)
print()
r6 = con.execute("SELECT SUM(is_bowler_wicket) FROM deliveries WHERE bowler LIKE '%Boult%' AND over < 3 AND bowling_team=home_team").fetchone()
print("Boult home first 3 overs wickets:", r6)

# Q8: Pat Cummins day/night
print()
r5 = con.execute("SELECT DISTINCT day_night FROM deliveries WHERE bowler LIKE '%Cummins%' AND match_type='Test'").fetchall()
print("Pat Cummins day_night values:", r5)
r5b = con.execute("SELECT COUNT(*) FROM deliveries WHERE bowler LIKE '%Cummins%' AND match_type='Test' AND day_night NOT IN ('Unknown','')").fetchone()
print("Day/Night test deliveries for Cummins:", r5b)

# Q9: Kagiso split Asia vs Non-Asia
print()
asia_countries = ('India','Pakistan','Sri Lanka','Bangladesh','UAE','Afghanistan')
r6a = con.execute("SELECT SUM(runs_total), COUNT(*)/6.0, SUM(is_bowler_wicket) FROM deliveries WHERE (bowler='Kagiso Rabada' OR bowler='K Rabada') AND country IN ('India','Pakistan','Sri Lanka','Bangladesh','UAE','Afghanistan')").fetchone()
r6b = con.execute("SELECT SUM(runs_total), COUNT(*)/6.0, SUM(is_bowler_wicket) FROM deliveries WHERE (bowler='Kagiso Rabada' OR bowler='K Rabada') AND country NOT IN ('India','Pakistan','Sri Lanka','Bangladesh','UAE','Afghanistan') AND country IS NOT NULL").fetchone()
eco_asia = round(r6a[0]/(r6a[1] or 1), 2) if r6a[0] else 0
eco_non  = round(r6b[0]/(r6b[1] or 1), 2) if r6b[0] else 0
print(f"Rabada economy ASIA: {eco_asia} (overs={round(r6a[1],1)}, wkts={r6a[2]})")
print(f"Rabada economy NON-ASIA: {eco_non} (overs={round(r6b[1],1)}, wkts={r6b[2]})")

# Q7: Ali Khan - which player?
print()
r7 = con.execute("SELECT DISTINCT bowler FROM deliveries WHERE bowler LIKE '%Ali Khan%'").fetchall()
print("Ali Khan variants:", r7)

# Q10: Bhuvi IPL innings 2 death exact numbers
print()
r10 = con.execute("SELECT SUM(is_bowler_wicket), SUM(runs_total), COUNT(*)/6.0 FROM deliveries WHERE (bowler='B Kumar' OR bowler='Bhuvneshwar Kumar') AND competition='Indian Premier League' AND innings=2 AND match_phase='Death'").fetchone()
print("Bhuvi IPL death 2nd innings wkts/runs/overs:", r10)
eco10 = round(r10[1]/(r10[2] or 1), 2) if r10[1] else 0
print("Economy:", eco10)

con.close()
