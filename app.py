import os
import re
from functools import wraps

from flask import Flask, jsonify, request
from flask_cors import CORS
import gspread
from google.oauth2.service_account import Credentials

from news_db import latest                # hämtar artiklar från SQLite
from rss_ai  import fetch_and_summarize   # <-- NYTT: webhook-funktion

# ────────── Google Sheets ──────────
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

CREDS_PATH     = "/etc/secrets/service_account.json"
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
ADMIN_TOKEN    = os.environ.get("ADMIN_TOKEN")

creds = Credentials.from_service_account_file(CREDS_PATH, scopes=SCOPES)
gc    = gspread.authorize(creds)
sh    = gc.open_by_key(SPREADSHEET_ID)

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

# ────────── 1. /api/settings ──────────
@app.route("/api/settings")
def settings():
    ws = sh.worksheet("Inställningar")
    return jsonify(ws.get_all_records())

# ────────── 2. /api/news ──────────
@app.route("/api/news")
def news():
    return jsonify(latest(20))            # 20 senaste artiklarna

# ────────── 3. /api/subscribe ──────────
EMAIL_RE = re.compile(r"^[^@]+@[^@]+\.[^@]+$")

@app.route("/api/subscribe", methods=["POST"])
def subscribe():
    data = request.get_json(silent=True) or {}
    name       = (data.get("name") or "").strip()
    email      = (data.get("email") or "").strip().lower()
    categories = data.get("categories") or []

    if not name:
        return jsonify({"error": "Name is required"}), 400
    if not EMAIL_RE.match(email):
        return jsonify({"error": "Valid email required"}), 400
    if not isinstance(categories, list):
        return jsonify({"error": "Categories must be a list"}), 400

    cat_str = ",".join(categories) if categories else "ALL"

    ws = sh.worksheet("Prenumeranter")
    emails_lower = [e.lower() for e in ws.col_values(2)]

    try:
        row_idx = emails_lower.index(email) + 1
        ws.update(f"A{row_idx}:C{row_idx}", [[name, email, cat_str]])
        return jsonify({"updated": True}), 200
    except ValueError:
        ws.append_row([name, email, cat_str])
        return jsonify({"created": True}), 201

# ────────── 4. /api/subscribers ──────────
@app.route("/api/subscribers")
@admin_required
def subscribers():
    ws = sh.worksheet("Prenumeranter")
    return jsonify(ws.get_all_records())

# ────────── 5. /api/delete-subscriber ──────────
@app.route("/api/delete-subscriber", methods=["POST"])
@admin_required
def delete_subscriber():
    email = (request.get_json(silent=True) or {}).get("email", "").lower().strip()
    if not email:
        return jsonify({"error": "Email required"}), 400

    ws = sh.worksheet("Prenumeranter")
    emails_lower = [e.lower() for e in ws.col_values(2)]
    rows = [i + 1 for i, e in enumerate(emails_lower) if e == email]
    if not rows:
        return jsonify({"error": "Email not found"}), 404

    for idx in sorted(rows, reverse=True):
        ws.delete_rows(idx)

    return jsonify({"deleted_rows": len(rows)}), 200

# ────────── 6. Webhook för GitHub Actions ──────────
@app.route("/admin/run-fetch", methods=["POST"])
@admin_required
def run_fetch():
    fetch_and_summarize()
    return jsonify({"ok": True}), 200

# ────────── Root ──────────
@app.route("/")
def index():
    return "AI-Nyheter API v1", 200

if __name__ == "__main__":
    app.run(debug=True, port=5000)
