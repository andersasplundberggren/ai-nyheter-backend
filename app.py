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

if __name__ == "__main__":
    app.run(debug=True, port=5000)
