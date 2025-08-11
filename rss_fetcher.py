# rss_fetcher.py
import os, re, sys, hashlib, html, time, logging
from datetime import datetime, timezone
from urllib.parse import urlparse

import feedparser
import gspread
from dateutil.parser import parse as dtparse
from google.oauth2.service_account import Credentials
from openai import OpenAI  # OpenAI 1.x

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
    """Skapa flikar om de saknas och säkerställ headers.
       Returnerar (ws_settings, ws_articles).
    """
    import gspread

    # Inställningar
    try:
        ws_settings = sh.worksheet("Inställningar")
    except gspread.WorksheetNotFound:
        ws_settings = sh.add_worksheet(title="Inställningar", rows=2, cols=3)
        ws_settings.append_row(["Kategori", "Källa", "Nyckelord"])
        log.info("Skapade fliken 'Inställningar' – lägg till rader innan körning.")

    # Artiklar
    desired = ["id", "title", "url", "date", "summary", "category", "paywall", "import_date"]
    try:
        ws_articles = sh.worksheet("Artiklar")
        header = ws_articles.row_values(1)
        if not header:
            ws_articles.update("A1:H1", [desired])
            log.info("Skrev headers i tom flik 'Artiklar'.")
        else:
            # Lägg till import_date om den saknas
            normalized = [h.strip().lower() for h in header]
            if "import_date" not in normalized:
                next_col = len(header) + 1
                ws_articles.update_cell(1, next_col, "import_date")
                log.info("La till saknad kolumn 'import_date' i 'Artiklar'.")
    except gspread.WorksheetNotFound:
        ws_articles = sh.add_worksheet(title="Artiklar", rows=1, cols=8)
        ws_articles.append_row(desired)
        log.info("Skapade fliken 'Artiklar' med headers.")

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
    """Städa upp en cell med en/ﬂera URL:er → unik lista med schema."""
    if not raw:
        return []
    parts = re.split(r"[\n,; \t]+", str(raw).strip())
    out = []
    for p in parts:
        if not p:
            continue
        u = p.strip()
        # lägg på https:// om schema saknas men det ser ut som en host
        if not re.match(r"^https?://", u, re.I):
            if re.match(r"^[\w.-]+\.[a-z]{2,}(/.*)?$", u, re.I):
                u = "https://" + u
        if re.match(r"^https?://", u, re.I):
            out.append(u)
    # unika + bevara ordning
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq

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

def matches_keywords(title: str, summary: str, keywords_str: str) -> bool:
    """Returnerar True om keywords-strängen är tom, eller om minst ett nyckelord matchar."""
    if not keywords_str:
        return True
    kws = [k.strip().lower() for k in re.split(r"[,;]+", keywords_str) if k.strip()]
    if not kws:
        return True
    text = f"{title} {summary}".lower()
    return any(k in text for k in kws)

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
        category  = (row.get("Kategori") or "").strip() or "Okänd"
        feeds     = normalize_feeds(row.get("Källa"))
        keywords  = (row.get("Nyckelord") or "").strip()

        if not feeds:
            continue

        log.info(f"{category}: {len(feeds)} feed(s)")
        for f in feeds:
            log.info(f"  feed: {f}")

        for feed_url in feeds:
            try:
                parsed = feedparser.parse(feed_url)
            except Exception as e:
                log.info(f"  Fel vid parse av {feed_url}: {e}")
                continue

            log.info(f"  {feed_url} → {len(parsed.entries)} entries")
            added_this_feed = 0

            for entry in parsed.entries[:MAX_ENTRIES_PER_FEED]:
                url   = entry.get("link")
                title = html.unescape(entry.get("title") or "").strip()
                entry_summary = entry.get("summary", "")

                if not url:
                    log.info("    - skip: saknar link")
                    continue
                if not title:
                    log.info(f"    - skip: saknar title ({url})")
                    continue
                if not matches_keywords(title, entry_summary, keywords):
                    log.info("    - skip: matchar ej nyckelord")
                    continue

                _id = sha1_id(url)
                if _id in existing_ids:
                    log.info(f"    - dup: {title[:60]}{'...' if len(title)>60 else ''}")
                    continue  # dedupe

                # datum
                raw_date = entry.get("published") or entry.get("updated") or ""
                date = parse_date(raw_date)
                import_date = datetime.now(timezone.utc).date().isoformat()

                # summarization
                summary = summarize_sv(title, url)

                # paywall
                paywall = is_paywalled(url, title, entry_summary)

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

                existing_ids.add(_id)  # undvik dubbletter i samma körning
                added_this_feed += 1
                log.info(f"    + add: {title[:60]}{'...' if len(title)>60 else ''}")
                time.sleep(SLEEP_BETWEEN_ITEMS)

            log.info(f"  {feed_url} → klart, nya i denna feed: {added_this_feed} (totalt stacked: {len(new_rows)})")

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
