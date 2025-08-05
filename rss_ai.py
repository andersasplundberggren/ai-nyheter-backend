import os, hashlib, time, html, re
import feedparser
from dateutil.parser import parse as dt
from datetime import datetime
from news_db import init, insert, latest
from openai import OpenAI            # NY import

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def fetch_and_summarize():
    from app import sh               # lazy-import mot cirkel­problem

    init()
    rows = sh.worksheet("Inställningar").get_all_records()

    for row in rows:
        category  = row["Kategori"]
        raw_feeds = row["Källa"] or ""
        feeds = [u.strip() for u in re.split(r'[,\s]+', raw_feeds) if u.strip()]

        for feed_url in feeds:
            parsed = feedparser.parse(feed_url)
            for entry in parsed.entries[:10]:
                art_id = hashlib.sha1(entry.link.encode()).hexdigest()
                if any(a["id"] == art_id for a in latest(1)):
                    continue

                title = html.unescape(entry.title)
                url   = entry.link

                raw_date = entry.get("published") or entry.get("updated") or ""
                try:
                    date = dt(raw_date).date().isoformat()
                except Exception:
                    date = datetime.utcnow().date().isoformat()

                # OpenAI-sammanfattning (1.x-anrop)
                try:
                    resp = client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[{
                          "role": "user",
                          "content": (
                            "Sammanfatta följande nyhetsartikel på svenska i max 50 ord.\n\n"
                            f"Titel: {title}\nLänk: {url}"
                          )}],
                        max_tokens=120,
                        temperature=0.2
                    )
                    summary = resp.choices[0].message.content.strip()
                except Exception as e:
                    print("OpenAI-fel:", e)
                    continue

                insert((art_id, title, url, date, summary, category))
                time.sleep(1)
