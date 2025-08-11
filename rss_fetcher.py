# rss_fetcher.py
import os, re, sys, hashlib, html, time, logging
from datetime import datetime, timezone
from urllib.parse import urlparse

import feedparser
import gspread
from dateutil.parser import parse as dtparse
from google.oauth2.service_account import Credentials

# OpenAI (1.x)
from openai import OpenAI

# ──────────────────────────────────────────────────────────────
# 0) Loggning
# ──────────────────────────────────────────────────────────────
log = logging.getLogger("fetcher")
handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(logging.Formatter("[fetcher] %(message)s"))
log.addHandler(handler)
log.setLevel(logging.INFO)

# ──────────────────────────────────────────────────────────────
# 1) Konfiguration (env)
# ──────────────────────────────────────────────────────────────
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
CREDS_PATH     = os.getenv("GOOGLE_CREDS_PATH", "/etc/secrets/service_account.json")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

MAX_ENTRIES_PER_FEED = int(os.getenv("MAX_ENTRIES_PER_FEED", "10"))
SLEEP_BETWEEN_ITEMS  = float(os.getenv("SLEEP_BETWEEN_ITEMS", "0.4"))

# Paywall-heuristik
PAYWALL_DOMAINS = {
    "dn.se", "svd.se", "ft.com", "nytimes.com", "theguardian.com", "kvalitetsmagasinet.se",
}
PAYWALL_HINTS = ("premium", "subscriber", "betalvägg", "paywall", "prenumeration")

# ──────────────────────────────────────────────────────────────
# 2) Klienter
# ──────────────────────────────────────────────────────────────
def get_sheet_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(CREDS_PATH, scopes=scopes)
    gc    = gspread.authorize(creds)
    return gc.open_by_key(SPREADSHEET_ID)

openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ──────────────────────────────────────────────────────────────
# 3) Hjälpare
# ──────────────────────────────────────────────────────────────
def ensure_worksheets(sh):
    """Skapa flikar om de saknas. Returnerar (ws_settings, ws_articles)."""
    try:
        ws_settings = sh.worksheet("Inställningar")
    except gspread.WorksheetNotFound:
        ws_settings = sh.add_worksheet(title="Inställningar", rows=2, cols=2)
        ws_settings.append_row(["Kategori", "Källa"])
        log.info("Skapade fliken 'Inställningar' (lägg till rader innan körning).")

    try:
        ws_articles = sh.worksheet("Artiklar")
    except gspread.WorksheetNotFound:
        ws_articles = sh.add_worksheet(title="Artiklar", rows=1, cols=8)
        ws_articles.append_row([
            "id", "title", "url", "date", "summary", "category", "paywall", "import_date"
        ])
        log.info("Skapade fliken 'Artiklar'.")

    return ws_settings, ws_articles

def get_existing_ids(ws_articles):
    """Hämta alla redan kända id:n (kol A, exkl. header)."""
    try:
        ids = ws_articles.col_values(1)[1:]  # hoppa header
        return set(ids)
    except Exception as e:
        log.info(f"Kunde inte läsa existerande id:n: {e}")
        return set()

def normalize_feeds(raw):
    """Ta en cell med en eller flera URL:er och returnera lista."""
    if not raw:
        return []
    parts = re.split(r"[\n,; ]+", str(raw).strip())
    return [p.strip() for p in parts if p.strip().startswith("http")]

def sha1_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()

def parse_date(value: str) -> str:
    """Returnera YYYY-MM-DD, fallback till dagens datum (UTC)."""
    if not value:
        return datetime.now(timezone.utc).date().isoformat()
    try:
        return dtparse(value).date().isoformat()
    except Exception:
        return datetime.now(timezone.utc).date().isoformat()

def is_paywalled(url: str, title: str = "", summary: str = "") -> bool:
    domain = urlparse(url).netloc.replace("www.", "").lower()
    if domain in PAYWALL_DOMAINS:
        return True
    text = f"{title} {summary}".lower()
    return any(h in text for h in PAYWALL_HINTS)

def summarize_sv(title: str, url: str) -> str:
    """Kort svensk sammanfattning via OpenAI. Fail-safe: tom sträng vid fel eller saknad nyckel."""
    if not openai_client:
        return ""
    try:
        resp = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": (
                    "Sammanfatta nyheten på svenska i max 40 ord. "
                    "Ingen rubrik, inga emojis. Vad har hänt och varför spelar det roll?\n\n"
                    f"Titel: {title}\nLänk: {url}"
                ),
            }],
            max_tokens=120,
            temperature=0.2,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        log.info(f"OpenAI-fel: {e}")
        return ""

# ──────────────────────────────────────────────────────────────
# 4) Huvudflöde
# ──────────────────────────────────────────────────────────────
def fetch_and_append() -> int:
    if not SPREADSHEET_ID:
        raise RuntimeError("Saknar SPREADSHEET_ID")
    sh = get_sheet_client()
    ws_settings, ws_articles = ensure_worksheets(sh)

    # Läs inställningar
    settings = ws_settings.get_all_records()
    if not settings:
        log.info("Inställningar är tom – inget att göra.")
        return 0

    # Läs existerande id:n (för dedupe)
    existing_ids = get_existing_ids(ws_articles)
    log.info(f"Existerande artiklar i Sheet: {len(existing_ids)}")

    # Hämta och bygg rader
    new_rows = []
    for row in settings:
        category = (row.get("Kategori") or "").strip() or "Okänd"
        feeds = normalize_feeds(row.get("Källa"))

        if not feeds:
            continue

        log.info(f"{category}: {len(feeds)} feed(s)")
        for feed_url in feeds:
            try:
                parsed = feedparser.parse(feed_url)
            except Exception as e:
                log.info(f"Fel vid parse av {feed_url}: {e}")
                continue

            log.info(f"  {feed_url} → {len(parsed.entries)} entries")
            for entry in parsed.entries[:MAX_ENTRIES_PER_FEED]:
                url   = entry.get("link")
                title = html.unescape(entry.get("title") or "").strip()

                if not url or not title:
                    continue

                _id = sha1_id(url)
                if _id in existing_ids:
                    continue  # dedupe

                # datum
                raw_date = entry.get("published") or entry.get("updated") or ""
                date = parse_date(raw_date)
                import_date = datetime.now(timezone.utc).date().isoformat()

                # summarization
                summary = summarize_sv(title, url)

                # paywall
                paywall = is_paywalled(url, title, entry.get("summary", ""))

                # bygg rad (exakt kolumnordning)
                new_rows.append([
                    _id,
                    title,
                    url,
                    date,
                    summary,
                    category,
                    "TRUE" if paywall else "FALSE",
                    import_date,
                ])

                existing_ids.add(_id)  # undvik dubletter i samma körning
                log.info(f"    + {title[:60]}{'...' if len(title)>60 else ''}")
                time.sleep(SLEEP_BETWEEN_ITEMS)

    # Batch-append
    if new_rows:
        ws_articles.append_rows(new_rows, value_input_option="USER_ENTERED")
        log.info(f"KLART: {len(new_rows)} nya artiklar tillagda.")
    else:
        log.info("Inga nya artiklar hittades.")

    return len(new_rows)

if __name__ == "__main__":
    try:
        added = fetch_and_append()
        log.info(f"Done. Added: {added}")
    except Exception as e:
        log.info(f"FATAL: {e}")
        sys.exit(1)
