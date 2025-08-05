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

CREDS_PATH     = "/etc/secrets/service_account.json"
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")          # måste finnas i Render env
ADMIN_TOKEN    = os.environ.get("ADMIN_TOKEN")             # lägger vi strax

creds = Credentials.from_service_account_file(CREDS_PATH, scopes=SCOPES)
gc    = gspread.authorize(creds)
sh    = gc.open_by_key(SPREADSHEET_ID)

# ────────── Flask ──────────
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ────────── Hjälpdekorator: admin-header ──────────
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

# ────────── 2. /api/news (GET) ──────────
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

# ────────── 3. /api/subscribe (POST) ──────────
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
    ws.append_row([name, email, cat_str])
    return jsonify({"ok": True}), 201

# ────────── 4. /api/subscribers (GET) ──────────
@app.route("/api/subscribers")
@admin_required
def subscribers():
    ws = sh.worksheet("Prenumeranter")
    return jsonify(ws.get_all_records())

# ────────── 5. /api/delete-subscriber (POST) ──────────
@app.route("/api/delete-subscriber", methods=["POST"])
@admin_required
def delete_subscriber():
    email = (request.get_json(silent=True) or {}).get("email", "").lower().strip()
    if not email:
        return jsonify({"error": "Email required"}), 400

    ws = sh.worksheet("Prenumeranter")
    emails = ws.col_values(2)                       # kolumn E-post
    try:
        idx = [e.lower() for e in emails].index(email) + 1  # Sheets är 1-baserat
        ws.delete_rows(idx)
        return jsonify({"deleted": True}), 200
    except ValueError:
        return jsonify({"error": "Email not found"}), 404

# ────────── Rot - bara info ──────────
@app.route("/")
def index():
    return "AI-Nyheter API v1", 200

if __name__ == "__main__":
    app.run(debug=True, port=5000)
