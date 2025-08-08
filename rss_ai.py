# rss_ai.py
import os, hashlib, time, html, re, sys
from datetime import datetime
from urllib.parse import urlparse

import feedparser
import gspread                              # ← för WorksheetNotFound
from dateutil.parser import parse as dt
from openai import OpenAI

from news_db import init, insert, exists


# ---------- DEBUG-HJÄLP ----------
def dbg(msg: str):
    print("[rss_ai]", msg, file=sys.stderr)


# ---------- OpenAI-klient ----------
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# ---------- Paywall-regler ----------
PAYWALL_DOMAINS = {
    "dn.se", "svd.se", "ft.com", "nytimes.com", "theguardian.com", "kvalitetsmagasinet.se",
}
PAYWALL_HINTS = ("premium", "subscriber", "betalvägg", "paywall")


# ---------- Huvudfunktion ----------
def fetch_and_summarize():
    dbg("Startar RSS/AIs-jobb")

    # bryter cirkelimport
    from app import sh

    init()  # säkerställ att SQLite är initierad

    # ── Google Sheet: Artiklar ──
    try:
        art_ws = sh.worksheet("Artiklar")
    except gspread.WorksheetNotFound:
        art_ws = sh.add_worksheet(title="Artiklar", rows=1, cols=8)
        art_ws.append_row(
            ["id", "title", "url", "date", "summary", "category", "paywall", "import_date"]
        )
        dbg("Skapade fliken 'Artiklar'")

    # ── Loopar över inställningar ──
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

            for entry in parsed.entries[:10]:  # begränsa till max 10 per feed
                url = entry.get("link")
                if not url or exists(url):
                    continue  # hoppa över dubbletter eller saknade länkar

                art_id = hashlib.sha1(url.encode()).hexdigest()
                title = html.unescape(entry.get("title", "")).strip()
                raw_date = entry.get("published") or entry.get("updated") or ""
                import_date = datetime.utcnow().date().isoformat()

                try:
                    date = dt(raw_date).date().isoformat()
                except Exception:
                    date = import_date

                # paywall?
                domain = urlparse(url).netloc.replace("www.", "")
                is_paywall = domain in PAYWALL_DOMAINS or any(
                    h in (entry.get("title", "") + entry.get("summary", "")).lower()
                    for h in PAYWALL_HINTS
                )

                # sammanfattning
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

                # ── Spara i SQLite ──
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

                # ── Spegla till Google Sheet ──
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
                time.sleep(1)  # artighetspaus
