# news_db.py
import sqlite3, contextlib, pathlib

DB_PATH = pathlib.Path("news.sqlite")

def init():
    with connect() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS articles(
            id       TEXT PRIMARY KEY,
            title    TEXT,
            url      TEXT,
            date     TEXT,
            summary  TEXT,
            category TEXT
            paywall   INTEGER DEFAULT 0
        )""")

@contextlib.contextmanager
def connect():
    con = sqlite3.connect(DB_PATH)
    try:
        yield con
    finally:
        con.commit()
        con.close()

def insert(article_tuple):
    with connect() as con:
        con.execute(
            "INSERT OR IGNORE INTO articles VALUES (?,?,?,?,?,?)",
            article_tuple
        )

def latest(limit=20):
    with connect() as con:
        cur  = con.execute(
            "SELECT * FROM articles ORDER BY date DESC LIMIT ?",
            (limit,)
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
