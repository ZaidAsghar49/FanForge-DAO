import sqlite3
import pandas as pd
from scripts.pipeline.city_map import CITY_COUNTRY_MAP

conn = sqlite3.connect("cricket.db")
query = "SELECT DISTINCT city, venue_name, match_type FROM deliveries WHERE batter = 'Babar Azam'"
df = pd.read_sql_query(query, conn)
print("Unique cities/venues for Babar Azam:")
print(df)

def _city_to_country(city):
    return CITY_COUNTRY_MAP.get(city, "Unknown")

df['resolved_country'] = df['city'].apply(_city_to_country)
print("\nResolved Countries:")
print(df[df['resolved_country'] == 'England'])

print("\nUnique match types:")
print(df['match_type'].unique())

conn.close()
