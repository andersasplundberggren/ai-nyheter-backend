import os
from flask import Flask, jsonify, request
import gspread
from google.oauth2.service_account import Credentials

# -------- Google Sheets ----------
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ❶ Render monterar nyckeln som fil /etc/secrets/service_account.json
CREDS_PATH     = "/etc/secrets/service_account.json"
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")

creds = Credentials.from_service_account_file(CREDS_PATH, scopes=SCOPES)
gc    = gspread.authorize(creds)
sh    = gc.open_by_key(SPREADSHEET_ID)

# -------- Flask ----------
app = Flask(__name__)

@app.route("/api/settings")
def settings():
    """Returnerar alla rader i fliken 'Inställningar'."""
    ws = sh.worksheet("Inställningar")
    return jsonify(ws.get_all_records())  # list[dict]

# – fler endpoints lägger vi till efter hand –

# -------- Hårdkodade nyheter (demo) ----------
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
    """Returnerar hårdkodade demo-nyheter tills vi kopplar RSS + AI."""
    # Sortera senaste först
    sorted_news = sorted(DEMO_NEWS, key=lambda n: n["date"], reverse=True)
    return jsonify(sorted_news)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
