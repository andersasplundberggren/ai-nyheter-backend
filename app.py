import os
import re
from flask import Flask, jsonify, request
from flask_cors import CORS
import gspread
from google.oauth2.service_account import Credentials

# ────────── Google Sheets ──────────
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

CREDS_PATH     = "/etc/secrets/service_account.json"         # Render Secret File
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")            # sätts i Render-env

creds = Credentials.from_service_account_file(CREDS_PATH, scopes=SCOPES)
gc    = gspread.authorize(creds)
sh    = gc.open_by_key(SPREADSHEET_ID)

# ────────── Flask ──────────
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})           # öppna CORS för API

# ────────── 1. Settings (GET) ──────────
@app.route("/api/settings")
def settings():
    """Returnerar alla rader i fliken 'Inställningar'."""
    ws = sh.worksheet("Inställningar")
    return jsonify(ws.get_all_records())                      # list[dict]

# ────────── 2. Hårdkodade nyheter (GET) ──────────
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
    """Returnerar hårdkodade demo-nyheter tills RSS + AI kopplas på."""
    return jsonify(sorted(DEMO_NEWS, key=lambda n: n["date"], reverse=True))

# ────────── 3. Subscribe (POST) ──────────
EMAIL_RE = re.compile(r"^[^@]+@[^@]+\.[^@]+$")

@app.route("/api/subscribe", methods=["POST"])
def subscribe():
    """Lägger till prenumerant i fliken 'Prenumeranter'."""
    data = request.get_json(silent=True) or {}
    name       = (data.get("name") or "").strip()
    email      = (data.get("email") or "").strip().lower()
    categories = data.get("categories") or []

    # — validering —
    if not name:
        return jsonify({"error": "Name is required"}), 400
    if not EMAIL_RE.match(email):
        return jsonify({"error": "Valid email required"}), 400
    if not isinstance(categories, list):
        return jsonify({"error": "Categories must be a list"}), 400

    cat_str = ",".join(categories) if categories else "ALL"

    try:
        ws = sh.worksheet("Prenumeranter")
        ws.append_row([name, email, cat_str])
        return jsonify({"ok": True}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ────────── (Frivillig) Rot-route ──────────
@app.route("/")
def index():
    return "AI-Nyheter API v1", 200

# ────────── Main (lokal körning) ──────────
if __name__ == "__main__":
    app.run(debug=True, port=5000)
