# rss_ai.py
import os, hashlib, time, html, re, sys
from datetime import datetime
from urllib.parse import urlparse

import feedparser
from dateutil.parser import parse as dt
from openai import OpenAI

from news_db import init, insert, latest

# ---------- DEBUG-HJÄLP ----------
def dbg(msg: str):
    """Skriv rad till stderr så den syns i Render-loggen."""
    print("[rss_ai]", msg, file=sys.stderr)

# ---------- OpenAI-klient ----------
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ---------- Paywall-regler ----------
PAYWALL_DOMAINS = {
    "dn.se", "svd.se", "ft.com", "nytimes.com",
    "theguardian.com",
}
PAYWALL_HINTS = ("premium", "subscriber", "betalvägg", "paywall")

# ---------- Huvudfunktion ----------
def fetch_and_summarize():
    dbg("startar job")

    # bryter cirkel-import
    from app import sh

    init()   # säkerställ tabellen

    rows = sh.worksheet("Inställningar").get_all_records()
    dbg(f"rader i Inställningar: {len(rows)}")

    for row in rows:
        category  = row["Kategori"].strip() or "Okänd"
        raw_feeds = row["Källa"] or ""
        feeds = [u.strip() for u in re.split(r'[,\s]+', raw_feeds) if u.strip()]
        if not feeds:
            continue

        dbg(f"{category}: {len(feeds)} feeds")
        for feed_url in feeds:
            dbg(f"  parse {feed_url}")
            parsed = feedparser.parse(feed_url)
            dbg(f"    entries: {len(parsed.entries)}")

            for entry in parsed.entries[:10]:     # max 10 per feed
                art_id = hashlib.sha1(entry.link.encode()).hexdigest()
                if any(a["id"] == art_id for a in latest(1)):
                    continue                      # redan sparad

                title = html.unescape(entry.title)
                url   = entry.link

                # datum
                raw_date = entry.get("published") or entry.get("updated") or ""
                try:
                    date = dt(raw_date).date().isoformat()
                except Exception:
                    date = datetime.utcnow().date().isoformat()

                # ---- Paywall-flagga ----
                domain = urlparse(url).netloc.replace("www.", "")
                is_paywall = (
                    domain in PAYWALL_DOMAINS or
                    any(h in (entry.get("title","") + entry.get("summary","")).lower()
                        for h in PAYWALL_HINTS)
                )

                # ---- OpenAI-sammanfattning ----
                try:
                    resp = client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[{
                            "role": "user",
                            "content": (
                                "Sammanfatta följande nyhetsartikel på svenska "
                                "i max 50 ord.\n\n"
                                f"Titel: {title}\nLänk: {url}"
                            )
                        }],
                        max_tokens=120,
                        temperature=0.2,
                    )
                    summary = resp.choices[0].message.content.strip()
                except Exception as e:
                    dbg(f"OpenAI-fel: {e}")
                    continue

                insert((
                    art_id,
                    title,
                    url,
                    date,
                    summary,
                    category,
                    int(is_paywall),
                ))
                dbg(f"    + sparad {title[:40]}{'...' if len(title)>40 else ''}")
                time.sleep(1)   # artighet mot API + RSS
