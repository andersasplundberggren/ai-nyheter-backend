# news_db.py
import sqlite3, contextlib, pathlib, sys
from datetime import datetime, timedelta

DB_PATH = pathlib.Path("news.sqlite")


def init() -> None:
    """Skapar tabell + migrerar vid behov."""
    with connect() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS articles (
              id          TEXT PRIMARY KEY,
              title       TEXT,
              url         TEXT UNIQUE,
              date        TEXT,
              summary     TEXT,
              category    TEXT,
              paywall     INTEGER DEFAULT 0,
              import_date TEXT
            )
            """
        )
        try:
            con.execute("ALTER TABLE articles ADD COLUMN paywall INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            con.execute("ALTER TABLE articles ADD COLUMN import_date TEXT")
        except sqlite3.OperationalError:
            pass

    print("[init] articles-tabellen finns/skapades OK", file=sys.stderr)


@contextlib.contextmanager
def connect():
    con = sqlite3.connect(DB_PATH)
    try:
        yield con
    finally:
        con.commit()
        con.close()


def insert(row: tuple) -> None:
    with connect() as con:
        con.execute(
            """
            INSERT OR IGNORE INTO articles
            (id, title, url, date, summary, category, paywall, import_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )


def exists(url: str) -> bool:
    with connect() as con:
        cur = con.execute("SELECT COUNT(*) FROM articles WHERE url = ?", (url,))
        return cur.fetchone()[0] > 0


def latest(limit: int = 20) -> list[dict]:
    with connect() as con:
        cur = con.execute(
            """
            SELECT id, title, url, date, summary, category, paywall
            FROM articles
            ORDER BY import_date DESC
            LIMIT ?
            """,
            (limit,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def latest_filtered(days: int = 1, max_articles: int = 20) -> list[dict]:
    """Returnerar max_articles artiklar importerade de senaste `days` dagarna."""
    since_date = (datetime.utcnow() - timedelta(days=days)).date().isoformat()
    with connect() as con:
        cur = con.execute(
            """
            SELECT id, title, url, date, summary, category, paywall
            FROM articles
            WHERE import_date >= ?
            ORDER BY import_date DESC
            LIMIT ?
            """,
            (since_date, max_articles),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
