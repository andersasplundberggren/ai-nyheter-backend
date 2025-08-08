from flask import Flask, render_template, request, redirect, session, jsonify
import os, re, sys
from functools import wraps
from importlib import import_module
from threading import Thread
from urllib.parse import unquote_plus

import gspread
from google.oauth2.service_account import Credentials

from news_db import latest, init as db_init
from util_email import gen_token, send_confirm, send_goodbye

# ────────── Init ──────────
db_init()
print("[app] SQLite init klar", file=sys.stderr)

# ────────── Google Sheets ──────────
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
CREDS_PATH = "/etc/secrets/service_account.json"
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")

creds = Credentials.from_service_account_file(CREDS_PATH, scopes=SCOPES)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

# ────────── Flask ──────────
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev")

# ────────── Helpers ──────────
def admin_required_route(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("admin"):
            return redirect("/admin/panel")
        return fn(*a, **kw)
    return wrapper

# ────────── Adminpanel ──────────
@app.route("/admin/panel", methods=["GET", "POST"])
def admin_panel():
    if request.method == "POST":
        if request.form.get("token") == ADMIN_TOKEN:
            session["admin"] = True
            return redirect("/admin/panel")
        return render_template("admin.html", authed=False, error="Fel lösenord")

    subs = sh.worksheet("Prenumeranter").get_all_records()
    arts = sh.worksheet("Artiklar").get_all_records()
    return render_template("admin.html", authed=session.get("admin"), subs=len(subs), arts=len(arts))

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect("/admin/panel")

@app.route("/admin/panel/send-digest", methods=["POST"])
@admin_required_route
def admin_send_digest():
    from util_email import send_digest
    days = int(request.form.get("days", 1))
    max_articles = int(request.form.get("max_articles", 6))
    
    def job():
        with app.app_context():
            subs = sh.worksheet("Prenumeranter").get_all_records()
            send_digest(
                subscribers=subs,
                dryrun=False,
                force=True,
                test_to=None,
                days=days,
                max_articles=max_articles,
            )

    Thread(target=job, daemon=True).start()
    return redirect("/admin/panel")

@app.route("/admin/panel/fetch", methods=["POST"])
@admin_required_route
def admin_rss_fetch():
    Thread(target=lambda: import_module("rss_ai").fetch_and_summarize(), daemon=True).start()
    return redirect("/admin/panel")

@app.route("/admin/panel/trigger-action", methods=["POST"])
@admin_required_route
def trigger_github_action():
    import requests
    workflow = request.form.get("workflow")
    token = os.getenv("GH_TOKEN")
    repo = os.getenv("GH_REPO")

    if not (workflow and token and repo):
        return "Miljövariabler saknas", 400

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    r = requests.post(
        f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
        headers=headers,
        json={"ref": "main"},
        timeout=10,
    )
    print("[admin] Triggerade action:", r.status_code, file=sys.stderr)
    return redirect("/admin/panel")

if __name__ == "__main__":
    app.run(debug=True, port=5000)
