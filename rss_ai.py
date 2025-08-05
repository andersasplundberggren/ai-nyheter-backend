import os, hashlib, time, html
import feedparser, openai
from dateutil.parser import parse as dt
from datetime import datetime
from news_db import init, insert, latest
from app import sh   # återanvänder redan auktoriserat Google Sheet-objekt

openai.api_key = os.environ["OPENAI_API_KEY"]

def fetch_and_summarize():
    init()
    rows = sh.worksheet("Inställningar").get_all_records()

    for row in rows:
        category = row["Kategori"]
        feed_url = row["Källa"]
        if not feed_url:
            continue

        parsed = feedparser.parse(feed_url)
        for entry in parsed.entries[:10]:  # max 10 per feed
            art_id = hashlib.sha1(entry.link.encode()).hexdigest()
            # Skippa om redan sparad:
            if any(a["id"] == art_id for a in latest(1)):
                continue

            title = html.unescape(entry.title)
            url   = entry.link

            raw_date = entry.get("published") or entry.get("updated") or ""
            try:
                date = dt(raw_date).date().isoformat()
            except Exception:
                date = datetime.utcnow().date().isoformat()

            # OpenAI-sammanfattning
            prompt = (
                "Sammanfatta följande nyhetsartikel på svenska i max 50 ord.\n\n"
                f"Titel: {title}\n"
                f"Länk: {url}"
            )
            try:
                resp = openai.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=120,
                    temperature=0.2
                )
                summary = resp.choices[0].message.content.strip()
            except Exception as e:
                print("OpenAI-fel:", e)
                continue

            insert((art_id, title, url, date, summary, category))
            time.sleep(1)  # artighet mot API + RSS-server

if __name__ == "__main__":
    fetch_and_summarize()
