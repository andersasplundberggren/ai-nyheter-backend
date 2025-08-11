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
    """Ta en cell med URL:er → städa, lägg på schema vid behov, ta bort dubbletter, behåll ordning."""
    if not raw:
        return []
    parts = re.split(r"[\n,; \t]+", str(raw).strip())
    out = []
    for p in parts:
        if not p:
            continue
        u = p.strip()
        # lägg på https:// om det saknas schema men ser ut som en host/path
        if not re.match(r"^https?://", u, re.I):
            if re.match(r"^[\w.-]+\.[a-z]{2,}(/.*)?$", u, re.I):
                u = "https://" + u
        if re.match(r"^https?://", u, re.I):
            out.append(u)
    # unika men bevara ordning
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
        return datetime.now(timezone.utc).date().
