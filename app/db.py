import sqlite3
from pathlib import Path

DB_PATH = Path("data.db")

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            dsa_minutes INTEGER NOT NULL,
            ml_minutes INTEGER NOT NULL,
            sql_minutes INTEGER NOT NULL,
            cloud_minutes INTEGER NOT NULL,
            leetcode_solved INTEGER NOT NULL,
            sleep_hours REAL NOT NULL
        );
    """)
    conn.commit()
    conn.close()
