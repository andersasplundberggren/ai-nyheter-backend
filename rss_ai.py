import os, hashlib, time, html, re, sys
from datetime import datetime
from urllib.parse import urlparse

import feedparser
import gspread
from dateutil.parser import parse as dt
from openai import OpenAI

from news_db import init, insert, exists


def dbg(msg: str):
    print("[rss_ai]", msg, file=sys.stderr)


client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

PAYWALL_DOMAINS = {
    "dn.se", "svd.se", "ft.com", "nytimes.com", "theguardian.com", "kvalitetsmagasinet.se",
}
PAYWALL_HINTS = ("premium", "subscriber", "betalvägg", "paywall")


def already_in_sheet(worksheet, article_id: str) -> bool:
    """Kolla om artikel-id redan finns i Sheet (kolumn A)."""
    try:
        ids = worksheet.col_values(1)
        return article_id in ids
    except Exception as e:
        dbg(f"Fel vid kontroll av dubblett i Sheet: {e}")
        return False


def fetch_and_summarize():
    dbg("Startar RSS/AIs-jobb")

    from app import sh
    init()  # säkerställ att SQLite är initierad

    try:
        art_ws = sh.worksheet("Artiklar")
    except gspread.WorksheetNotFound:
        art_ws = sh.add_worksheet(title="Artiklar", rows=1, cols=8)
        art_ws.append_row(
            ["id", "title", "url", "date", "summary", "category", "paywall", "import_date"]
        )
        dbg("Skapade fliken 'Artiklar'")

    rows = sh.worksheet("Inställningar").get_all_records()
    dbg(f"Antal kategorirader: {len(rows)}")

    for row in rows:
        category = row.get("Kategori", "").strip() or "Okänd"
        raw_feeds = row.get("Källa", "")
        feeds = [u.strip() for u in re.split(r"[,\s]+", raw_feeds) if u.strip()]
        if not feeds:
            continue

        dbg(f"{category}: {len(feeds)} feeds")
        for feed_url in feeds:
            dbg(f"  Hämtar från: {feed_url}")
            parsed = feedparser.parse(feed_url)
            dbg(f"    {len(parsed.entries)} entries")

            for entry in parsed.entries[:10]:
                url = entry.get("link")
                if not url or exists(url):
                    continue

                art_id = hashlib.sha1(url.encode()).hexdigest()
                if already_in_sheet(art_ws, art_id):
                    continue

                title = html.unescape(entry.get("title", "")).strip()
                raw_date = entry.get("published") or entry.get("updated") or ""
                import_date = datetime.utcnow().date().isoformat()

                try:
                    date = dt(raw_date).date().isoformat()
                except Exception:
                    date = import_date

                domain = urlparse(url).netloc.replace("www.", "")
                is_paywall = domain in PAYWALL_DOMAINS or any(
                    h in (entry.get("title", "") + entry.get("summary", "")).lower()
                    for h in PAYWALL_HINTS
                )

                try:
                    resp = client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[
                            {
                                "role": "user",
                                "content": (
                                    "Sammanfatta följande nyhetsartikel på svenska "
                                    "i max 50 ord.\n\n"
                                    f"Titel: {title}\nLänk: {url}"
                                ),
                            }
                        ],
                        max_tokens=120,
                        temperature=0.2,
                    )
                    summary = resp.choices[0].message.content.strip()
                except Exception as e:
                    dbg(f"OpenAI-fel: {e}")
                    continue

                insert(
                    (
                        art_id,
                        title,
                        url,
                        date,
                        summary,
                        category,
                        int(is_paywall),
                        import_date,
                    )
                )

                art_ws.append_row(
                    [
                        art_id,
                        title,
                        url,
                        date,
                        summary,
                        category,
                        "1" if is_paywall else "0",
                        import_date,
                    ]
                )

                dbg(f"    + sparad: {title[:40]}{'...' if len(title) > 40 else ''}")
                time.sleep(1)


def remove_duplicates_from_sheet():
    """Rensar bort dubbletter i Artiklar-fliken baserat på artikel-ID."""
    from app import sh
    import time

    try:
        ws = sh.worksheet("Artiklar")
        rows = ws.get_all_values()
        header = rows[0]
        data = rows[1:]
    except Exception as e:
        print("[dup-rensning] Kunde inte läsa Sheet:", e, file=sys.stderr)
        return

    seen = set()
    rows_to_delete = []

    for idx, row in enumerate(data, start=2):  # börjar på rad 2
        art_id = row[0]
        if art_id in seen:
            rows_to_delete.append(idx)
        else:
            seen.add(art_id)

    if not rows_to_delete:
        print("[dup-rensning] Inga dubbletter hittades", file=sys.stderr)
        return

    print(f"[dup-rensning] Hittade {len(rows_to_delete)} dubbletter – rensar...", file=sys.stderr)

    for row_num in reversed(rows_to_delete):
        try:
            ws.delete_rows(row_num)
            time.sleep(1)
        except Exception as e:
            print(f"[dup-rensning] Misslyckades att ta bort rad {row_num}: {e}", file=sys.stderr)

    print("[dup-rensning] Klart.", file=sys.stderr)
