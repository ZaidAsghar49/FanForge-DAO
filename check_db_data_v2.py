import sqlite3
import pandas as pd

conn = sqlite3.connect("cricket.db")
query = "SELECT DISTINCT match_id, date, city, venue_name, batter, innings FROM deliveries WHERE batter LIKE '%Kohli%' AND date LIKE '2021%';"
df = pd.read_sql_query(query, conn)
print(df)
conn.close()
