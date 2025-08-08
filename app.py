# app.py – AI-Nyheter backend v2.6 (med webbaserad admin-login + GitHub Action-styrning)
import os, re, sys, json
from functools import wraps
from importlib import import_module
from threading import Thread
from urllib.parse import unquote_plus

from flask import Flask, jsonify, request, session, render_template, redirect, url_for
from flask_cors import CORS
import gspread
from google.oauth2.service_account import Credentials
import requests

from news_db import latest, init as db_init
from util_email import gen_token, send_confirm, send_goodbye  # send_digest importeras i tråd

# ────────── 0. Init SQLite vid cold-start ──────────
db_init()
print("[app] SQLite init klar", file=sys.stderr)

# ────────── 1. Google-Sheets-klient ──────────
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
CREDS_PATH     = "/etc/secrets/service_account.json"
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
ADMIN_TOKEN    = os.getenv("ADMIN_TOKEN")
ADMIN_PASS     = os.getenv("ADMIN_PASSWORD", "test123")
GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN")
GITHUB_REPO    = os.getenv("GITHUB_REPO")  # t.ex. "dittnamn/ai-nyheter"

creds = Credentials.from_service_account_file(CREDS_PATH, scopes=SCOPES)
gc    = gspread.authorize(creds)
sh    = gc.open_by_key(SPREADSHEET_ID)

# ────────── 2. Flask-app ──────────
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "hemligt")
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ────────── 3. Enkel admin-session ──────────
def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if session.get("admin_logged_in"):
            return fn(*a, **kw)
        return redirect("/admin/login")
    return wrapper

# ─────────────────────────────────────────────
#               GitHub Actions Trigger
# ─────────────────────────────────────────────

def trigger_github_action(workflow: str) -> bool:
    if not (GITHUB_TOKEN and GITHUB_REPO):
        print("[github] Token eller repo saknas", file=sys.stderr)
        return False

    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{workflow}/dispatches"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    data = {"ref": "main"}
    res = requests.post(url, headers=headers, data=json.dumps(data))
    print("[github] Trigger status:", res.status_code, file=sys.stderr)
    return res.status_code == 204

# ─────────────────────────────────────────────
#            Nya Admin-rutter: GitHub
# ─────────────────────────────────────────────

@app.route("/admin/panel/trigger-action", methods=["POST"])
@admin_required
def trigger_action():
    workflow = request.form.get("workflow", "fetch.yml")
    trigger_github_action(workflow)
    return redirect("/admin/panel")
