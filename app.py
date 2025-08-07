# app.py  – AI-Nyheter backend  (v2.1)
import os, re, sys
from functools   import wraps
from importlib   import import_module
from threading   import Thread
from urllib.parse import unquote_plus

from flask        import Flask, jsonify, request
from flask_cors   import CORS
import gspread
from google.oauth2.service_account import Credentials

from news_db      import latest, init as db_init
from util_email   import gen_token, send_confirm, send_goodbye, send_digest

# ────────── 0. Init SQLite vid cold-start ──────────
db_init()
print("[app] SQLite init klar", file=sys.stderr)

# ────────── 1. Google Sheets-klient ──────────
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
CREDS_PATH     = "/etc/secrets/service_account.json"
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
ADMIN_TOKEN    = os.getenv("ADMIN_TOKEN")          # sätts i Render/GitHub Secrets

creds = Credentials.from_service_account_file(CREDS_PATH, scopes=SCOPES)
gc    = gspread.authorize(creds)
sh    = gc.open_by_key(SPREADSHEET_ID)             # delas med rss_ai.py

# ────────── 2. Flask-app ──────────
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ────────── 3. Admin-header-skydd ──────────
def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if request.headers.get("X-Admin-Token") != ADMIN_TOKEN:
            return jsonify({"error": "Unauthorized"}), 401
        return fn(*a, **kw)
    return wrapper

# ────────────────────────────────────────────────────────────────
#  API-ENDPOINTS
# ────────────────────────────────────────────────────────────────

# 3.1  Inställningar (kategorier/feeds) --------------------------
@app.route("/api/settings")
def settings():
    return jsonify(sh.worksheet("Inställningar").get_all_records())

# 3.2  Senaste 6 nyheter (startsidan) ---------------------------
@app.route("/api/news")
def news():
    return jsonify(latest(6))

# 3.3  Arkiv (SQLite, paginerat/filtrerat) ----------------------
@app.route("/api/archive")
def archive():
    page = max(1, int(request.args.get("page", 1)))
    per  = max(5, int(request.args.get("per", 40)))

    cat = unquote_plus(request.args.get("cat", "")).lower()
    q   = unquote_plus(request.args.get("q",   "")).lower()

    arts = latest(2000)                                      # buffert
    if cat:
        arts = [a for a in arts if a["category"].lower() == cat]
    if q:
        arts = [a for a in arts if q in a["title"].lower() or q in a["summary"].lower()]

    off = (page - 1) * per
    return jsonify(arts[off:off + per])

# 3.3b Arkiv direkt från Google-Sheet (fallback) ----------------
@app.route("/api/archive-sheet")
def archive_sheet():
    try:
        return jsonify(sh.worksheet("Artiklar").get_all_records())
    except gspread.WorksheetNotFound:
        return jsonify([])

# 3.4  Prenumerera (dubbel opt-in) ------------------------------
EMAIL_RE = re.compile(r"^[^@]+@[^@]+\.[^@]+$")

@app.route("/api/subscribe", methods=["POST"])
def subscribe():
    data   = request.get_json(silent=True) or {}
    name   = (data.get("name")  or "").strip()
    email  = (data.get("email") or "").strip().lower()
    cats   = data.get("categories") or []

    if not name:                  return jsonify({"error": "Name required"}), 400
    if not EMAIL_RE.match(email): return jsonify({"error": "Invalid email"}), 400
    if not isinstance(cats, list):return jsonify({"error": "Categories list"}), 400

    cat_str = ",".join(cats) if cats else "ALL"
    token   = gen_token()

    ws      = sh.worksheet("Prenumeranter")
    rows    = ws.get_all_records()
    idx     = next((i+2 for i,r in enumerate(rows) if r["E-post"].lower()==email), None)

    if idx:  # uppdatera befintlig
        ws.update(f"A{idx}:E{idx}", [[name,email,cat_str,"pending",token]])
    else:    # ny prenumerant
        ws.append_row([name,email,cat_str,"pending",token])

    send_confirm(email, token)
    return jsonify({"msg": "Confirmation sent"}), 202

# 3.5  Bekräftelse-länk -----------------------------------------
@app.route("/api/confirm")
def confirm():
    email = request.args.get("email","").lower()
    tok   = request.args.get("tok","")
    ws    = sh.worksheet("Prenumeranter")

    for i,r in enumerate(ws.get_all_records(), start=2):
        if r["E-post"].lower()==email and r["Token"]==tok:
            ws.update(f"D{i}", "active")
            return "Prenumerationen är nu aktiverad ✅", 200
    return "Ogiltig eller förbrukad länk.", 400

# 3.6  Avsluta-länk ---------------------------------------------
@app.route("/api/unsubscribe")
def unsubscribe():
    email = request.args.get("email","").lower()
    tok   = request.args.get("tok","")
    ws    = sh.worksheet("Prenumeranter")

    for i,r in enumerate(ws.get_all_records(), start=2):
        if r["E-post"].lower()==email and r["Token"]==tok:
            ws.update(f"D{i}", "unsub")
            send_goodbye(email)
            return "Prenumerationen avslutad.", 200
    return "Ogiltig länk.", 400

# 3.7  Byta kategorier via token-skyddad POST -------------------
@app.route("/api/update-cats", methods=["POST"])
def update_cats():
    data  = request.get_json(silent=True) or {}
    email = data.get("email","").lower()
    tok   = data.get("tok","")
    cats  = data.get("cats",[])
    if not isinstance(cats,list): return "cats ska vara lista", 400

    ws = sh.worksheet("Prenumeranter")
    for i,r in enumerate(ws.get_all_records(), start=2):
        if r["E-post"].lower()==email and r["Token"]==tok:
            ws.update(f"C{i}", ",".join(cats) if cats else "ALL")
            return "", 204
    return "Fel token", 400

# ────────── ADMIN ENDPOINTS ──────────

# 4.1  Lista prenumeranter --------------------------------------
@app.route("/api/subscribers")
@admin_required
def subscribers():
    return jsonify(sh.worksheet("Prenumeranter").get_all_records())

# 4.2  Radera prenumerant ---------------------------------------
@app.route("/api/delete-subscriber", methods=["POST"])
@admin_required
def delete_subscriber():
    email = (request.get_json(silent=True) or {}).get("email","").lower()
    if not email: return jsonify({"error":"email"}),400

    ws   = sh.worksheet("Prenumeranter")
    rows = [i+2 for i,r in enumerate(ws.get_all_records()) if r["E-post"].lower()==email]
    for r in reversed(rows): ws.delete_rows(r)
    return jsonify({"deleted": len(rows)})

# 4.3  Kör RSS+AI-inhämtning i bakgrund -------------------------
@app.route("/admin/run-fetch", methods=["POST"])
@admin_required
def run_fetch():
    Thread(target=lambda: import_module("rss_ai").fetch_and_summarize(),
           daemon=True).start()
    return jsonify({"ok": True, "msg": "Fetch job started"}), 202

# 4.4  Skicka nyhetsbrev (digest) i bakgrund --------------------
@app.route("/admin/send-digest", methods=["POST"])
@admin_required
def send_digest_job():
    '''Skickar dagens / veckans digest till aktiva prenumeranter.'''
    def job():
        # plocka ut senaste 6 artiklar (eller justera valfritt)
        articles = latest(6)
        send_digest(sh.worksheet("Prenumeranter").get_all_records(), articles)
    Thread(target=job, daemon=True).start()
    return jsonify({"ok": True, "msg": "Digest job started"}), 202

# ────────── Root ping ──────────
@app.route("/")
def index():
    return "AI-Nyheter API v2.1", 200

if __name__ == "__main__":
    app.run(debug=True, port=5000)
