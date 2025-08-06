# news_db.py
import sqlite3
import contextlib
import pathlib

DB_PATH = pathlib.Path("news.sqlite")


# ----------- initiera / migrera ---------------------------------
def init() -> None:
    """Skapar tabellen om den saknas + lägger till paywall-kolumn vid behov."""
    with connect() as con:
        # 1. Skapa tabell om den inte finns
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS articles (
              id        TEXT PRIMARY KEY,
              title     TEXT,
              url       TEXT,
              date      TEXT,
              summary   TEXT,
              category  TEXT,
              paywall   INTEGER DEFAULT 0
            )
            """
        )
        # 2. Lägg till kolumnen paywall om tabellen skapades innan migreringen
        try:
            con.execute("ALTER TABLE articles ADD COLUMN paywall INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            # kolumnen finns redan – ignorera
            pass


# ----------- context-manager för DB-anslutningen ----------------
@contextlib.contextmanager
def connect():
    con = sqlite3.connect(DB_PATH)
    try:
        yield con
    finally:
        con.commit()
        con.close()


# ----------- INSERT ---------------------------------------------
def insert(article_tuple: tuple) -> None:
    """
    article_tuple måste innehålla sju fält:
      (id, title, url, date, summary, category, paywall_int)
    paywall_int = 1 om bakom betalvägg, annars 0
    """
    with connect() as con:
        con.execute(
            """
            INSERT OR IGNORE INTO articles
            (id, title, url, date, summary, category, paywall)
            VALUES (?,?,?,?,?,?,?)
            """,
            article_tuple,
        )


# ----------- hämta senaste --------------------------------------
def latest(limit: int = 20) -> list[dict]:
    with connect() as con:
        cur = con.execute(
            "SELECT id,title,url,date,summary,category,paywall "
            "FROM articles ORDER BY date DESC LIMIT ?",
            (limit,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
