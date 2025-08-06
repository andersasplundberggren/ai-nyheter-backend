import os, re
from functools import wraps
from importlib import import_module
from threading import Thread
from urllib.parse import unquote_plus

from flask import Flask, jsonify, request
from flask_cors import CORS
import gspread
from google.oauth2.service_account import Credentials

from news_db import latest, init as db_init   # ← init-funktionen

# ────────── Initiera DB vid varje cold-start ──────────
db_init()
print("[app] DB init klar")   # syns i Render-loggarna

# ────────── Google Sheets ──────────
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

CREDS_PATH     = "/etc/secrets/service_account.json"
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
ADMIN_TOKEN    = os.getenv("ADMIN_TOKEN")

creds = Credentials.from_service_account_file(CREDS_PATH, scopes=SCOPES)
gc    = gspread.authorize(creds)
sh    = gc.open_by_key(SPREADSHEET_ID)        # tillgängligt för rss_ai

# ────────── Flask ──────────
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ────────── Admin-dekorator ──────────
def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if request.headers.get("X-Admin-Token") != ADMIN_TOKEN:
            return jsonify({"error": "Unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapper

# 1. /api/settings -------------------------------------------------
@app.route("/api/settings")
def settings():
    ws = sh.worksheet("Inställningar")
    return jsonify(ws.get_all_records())

# 2. /api/news -----------------------------------------------------
@app.route("/api/news")
def news():
    return jsonify(latest(20))               # 20 senaste

# 3. /api/archive --------------------------------------------------
@app.route("/api/archive")
def archive():
    page = max(1, int(request.args.get("page", 1)))
    per  = max(5, int(request.args.get("per", 40)))

    cat_filter = unquote_plus(request.args.get("cat", "")).strip().lower()
    query      = unquote_plus(request.args.get("q",   "")).strip().lower()

    articles = latest(2000)                  # buffert

    if cat_filter:
        articles = [a for a in articles if a["category"].lower() == cat_filter]
    if query:
        articles = [
            a for a in articles
            if query in a["title"].lower() or query in a["summary"].lower()
        ]

    off = (page - 1) * per
    return jsonify(articles[off : off + per])

# 4. /api/subscribe -----------------------------------------------
EMAIL_RE = re.compile(r"^[^@]+@[^@]+\.[^@]+$")

@app.route("/api/subscribe", methods=["POST"])
def subscribe():
    data  = request.get_json(silent=True) or {}
    name  = (data.get("name")  or "").strip()
    email = (data.get("email") or "").strip().lower()
    cats  = data.get("categories") or []

    if not name:
        return jsonify({"error": "Name is required"}), 400
    if not EMAIL_RE.match(email):
        return jsonify({"error": "Valid email required"}), 400
    if not isinstance(cats, list):
        return jsonify({"error": "Categories must be a list"}), 400

    cat_str = ",".join(cats) if cats else "ALL"

    ws = sh.worksheet("Prenumeranter")
    emails_lower = [e.lower() for e in ws.col_values(2)]

    try:
        row = emails_lower.index(email) + 1
        ws.update(f"A{row}:C{row}", [[name, email, cat_str]])
        return jsonify({"updated": True})
    except ValueError:
        ws.append_row([name, email, cat_str])
        return jsonify({"created": True}), 201

# 5. /api/subscribers ---------------------------------------------
@app.route("/api/subscribers")
@admin_required
def subscribers():
    ws = sh.worksheet("Prenumeranter")
    return jsonify(ws.get_all_records())

# 6. /api/delete-subscriber ---------------------------------------
@app.route("/api/delete-subscriber", methods=["POST"])
@admin_required
def delete_subscriber():
    email = (request.get_json(silent=True) or {}).get("email", "").lower().strip()
    if not email:
        return jsonify({"error": "Email required"}), 400

    ws     = sh.worksheet("Prenumeranter")
    lowers = [e.lower() for e in ws.col_values(2)]
    rows   = [i + 1 for i, e in enumerate(lowers) if e == email]
    if not rows:
        return jsonify({"error": "Email not found"}), 404

    for r in reversed(rows):
        ws.delete_rows(r)

    return jsonify({"deleted_rows": len(rows)})

# 7. /admin/run-fetch  (körs i bakgrund) --------------------------
@app.route("/admin/run-fetch", methods=["POST"])
@admin_required
def run_fetch():
    def job():
        import_module("rss_ai").fetch_and_summarize()
    Thread(target=job, daemon=True).start()
    return jsonify({"ok": True, "msg": "Job started"}), 202

# Root ------------------------------------------------------------
@app.route("/")
def index():
    return "AI-Nyheter API v1", 200

if __name__ == "__main__":
    app.run(debug=True, port=5000)
