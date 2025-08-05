import os
import re
from functools import wraps

from flask import Flask, jsonify, request
from flask_cors import CORS
import gspread
from google.oauth2.service_account import Credentials

# ────────── Google Sheets ──────────
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

CREDS_PATH     = "/etc/secrets/service_account.json"          # Secret File på Render
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")             # env-variabel
ADMIN_TOKEN    = os.environ.get("ADMIN_TOKEN")                # env-variabel

creds = Credentials.from_service_account_file(CREDS_PATH, scopes=SCOPES)
gc    = gspread.authorize(creds)
sh    = gc.open_by_key(SPREADSHEET_ID)

# ────────── Flask ──────────
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})            # öppna CORS

# ────────── Admin-dekorator ──────────
def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = request.headers.get("X-Admin-Token")
        if token != ADMIN_TOKEN:
            return jsonify({"error": "Unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapper

# ────────── 1. /api/settings (GET) ──────────
@app.route("/api/settings")
def settings():
    ws = sh.worksheet("Inställningar")
    return jsonify(ws.get_all_records())

# ────────── 2. /api/news (GET) – demo ──────────
DEMO_NEWS = [
    {
        "id": 1,
        "title":  "OpenAI lanserar GPT-5-förhandsvisning",
        "summary": "Nya versionen fokuserar på tillförlitlighet och multimodalitet...",
        "url":   "https://example.com/openai-gpt5",
        "date":  "2025-08-04"
    },
    {
        "id": 2,
        "title":  "EU klubbar AI-förordningen (AI Act)",
        "summary": "Parlamentet har röstat igenom sluttexten som träder i kraft 2026...",
        "url":   "https://example.com/eu-ai-act",
        "date":  "2025-07-30"
    }
]

@app.route("/api/news")
def news():
    return jsonify(sorted(DEMO_NEWS, key=lambda n: n["date"], reverse=True))

# ────────── 3. /api/subscribe (POST) – duplicate-skydd ──────────
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
    emails_lower = [e.lower() for e in ws.col_values(2)]       # kolumn 2 = e-post

    try:
        # finns redan → uppdatera rad
        row_idx = emails_lower.index(email) + 1                # 1-baserat
        ws.update(f"A{row_idx}:C{row_idx}", [[name, email, cat_str]])
        return jsonify({"updated": True}), 200
    except ValueError:
        # ny prenumerant → lägg längst ned
        ws.append_row([name, email, cat_str])
        return jsonify({"created": True}), 201

# ────────── 4. /api/subscribers (GET) ──────────
@app.route("/api/subscribers")
@admin_required
def subscribers():
    ws = sh.worksheet("Prenumeranter")
    return jsonify(ws.get_all_records())

# ────────── 5. /api/delete-subscriber (POST) – ta bort alla träffar ──────────
@app.route("/api/delete-subscriber", methods=["POST"])
@admin_required
def delete_subscriber():
    email = (request.get_json(silent=True) or {}).get("email", "").lower().strip()
    if not email:
        return jsonify({"error": "Email required"}), 400

    ws = sh.worksheet("Prenumeranter")
    emails_lower = [e.lower() for e in ws.col_values(2)]

    rows_to_delete = [i + 1 for i, e in enumerate(emails_lower) if e == email]
    if not rows_to_delete:
        return jsonify({"error": "Email not found"}), 404

    for idx in sorted(rows_to_delete, reverse=True):
        ws.delete_rows(idx)

    return jsonify({"deleted_rows": len(rows_to_delete)}), 200

# ────────── Rotroute ──────────
@app.route("/")
def index():
    return "AI-Nyheter API v1", 200

if __name__ == "__main__":
    app.run(debug=True, port=5000)
