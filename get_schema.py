import sqlite3
import pandas as pd

conn = sqlite3.connect('cricket.db')
cursor = conn.cursor()
cursor.execute("PRAGMA table_info(deliveries)")
columns = cursor.fetchall()
for col in columns:
    print(col)
conn.close()
