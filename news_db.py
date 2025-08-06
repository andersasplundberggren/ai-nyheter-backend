# news_db.py
import sqlite3, contextlib, pathlib, sys

DB_PATH = pathlib.Path("news.sqlite")


def init() -> None:
    """Skapar tabell + migrerar vid behov."""
    with connect() as con:
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
        try:
            con.execute("ALTER TABLE articles ADD COLUMN paywall INTEGER DEFAULT 0")
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
            (id,title,url,date,summary,category,paywall)
            VALUES (?,?,?,?,?,?,?)
            """,
            row,
        )


def latest(limit: int = 20) -> list[dict]:
    with connect() as con:
        cur = con.execute(
            """
            SELECT id,title,url,date,summary,category,paywall
            FROM articles ORDER BY date DESC LIMIT ?
            """,
            (limit,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
