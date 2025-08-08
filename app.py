# app.py – AI-Nyheter backend v2.7 (komplett med adminpanel och GitHub-actionstyrning)
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

# ────────── 4. GitHub Actions Trigger ──────────
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

# ────────── 5. Publika endpoints ──────────
@app.route("/api/settings")
def settings():
    return jsonify(sh.worksheet("Inställningar").get_all_records())

@app.route("/api/news")
def news():
    return jsonify(latest(6))

@app.route("/api/archive")
def archive():
    page = max(1, int(request.args.get("page", 1)))
    per  = max(5, int(request.args.get("per", 40)))

    cat = unquote_plus(request.args.get("cat", "")).lower()
    q   = unquote_plus(request.args.get("q",   "")).lower()

    arts = latest(2000)
    if cat:
        arts = [a for a in arts if a["category"].lower() == cat]
    if q:
        arts = [a for a in arts if q in a["title"].lower() or q in a["summary"].lower()]

    off = (page - 1) * per
    return jsonify(arts[off:off + per])

@app.route("/api/archive-sheet")
def archive_sheet():
    try:
        return jsonify(sh.worksheet("Artiklar").get_all_records())
    except gspread.WorksheetNotFound:
        return jsonify([])

# ────────── 6. Prenumeration ──────────
EMAIL_RE = re.compile(r"^[^@]+@[^@]+\\.[^@]+$")

@app.route("/api/subscribe", methods=["POST"])
def subscribe():
    data   = request.get_json(silent=True) or {}
    name   = (data.get("name")  or "").strip()
    email  = (data.get("email") or "").strip().lower()
    cats   = data.get("categories") or []

    if not name:
        return jsonify({"error": "Name required"}), 400
    if not EMAIL_RE.match(email):
        return jsonify({"error": "Invalid email"}), 400
    if not isinstance(cats, list):
        return jsonify({"error": "Categories list"}), 400

    cat_str = ",".join(cats) if cats else "ALL"
    token   = gen_token()

    ws   = sh.worksheet("Prenumeranter")
    rows = ws.get_all_records()
    idx  = next((i + 2 for i, r in enumerate(rows) if r["E-post"].lower() == email), None)

    if idx:
        ws.update(f"A{idx}:E{idx}", [[name, email, cat_str, "pending", token]])
    else:
        ws.append_row([name, email, cat_str, "pending", token])

    send_confirm(email, token)
    return jsonify({"msg": "Confirmation sent"}), 202

@app.route("/api/confirm")
def confirm():
    email = request.args.get("email", "").lower()
    tok   = request.args.get("tok", "")
    ws    = sh.worksheet("Prenumeranter")

    for i, r in enumerate(ws.get_all_records(), start=2):
        if r["E-post"].lower() == email and r["Token"] == tok:
            ws.update(f"D{i}", "active")
            return "Prenumerationen är nu aktiverad ✅", 200
    return "Ogiltig eller förbrukad länk.", 400

@app.route("/api/unsubscribe")
def unsubscribe():
    email = request.args.get("email", "").lower()
    tok   = request.args.get("tok", "")
    ws    = sh.worksheet("Prenumeranter")

    for i, r in enumerate(ws.get_all_records(), start=2):
        if r["E-post"].lower() == email and r["Token"] == tok:
            ws.update(f"D{i}", "unsub")
            send_goodbye(email)
            return "Prenumerationen avslutad.", 200
    return "Ogiltig länk.", 400

@app.route("/api/update-cats", methods=["POST"])
def update_cats():
    data  = request.get_json(silent=True) or {}
    email = data.get("email", "").lower()
    tok   = data.get("tok", "")
    cats  = data.get("cats", [])
    if not isinstance(cats, list):
        return "cats ska vara lista", 400

    ws = sh.worksheet("Prenumeranter")
    for i, r in enumerate(ws.get_all_records(), start=2):
        if r["E-post"].lower() == email and r["Token"] == tok:
            ws.update(f"C{i}", ",".join(cats) if cats else "ALL")
            return "", 204
    return "Fel token", 400

# ────────── 7. Admin-gränssnitt ──────────
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        pw = request.form.get("password")
        if pw == ADMIN_PASS:
            session["admin_logged_in"] = True
            return redirect("/admin/panel")
        return "Fel lösenord", 403
    return """<form method='POST'><input type='password' name='password' placeholder='Lösenord'><button>Logga in</button></form>"""

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/")

@app.route("/admin/panel")
@admin_required
def admin_panel():
    subs = len([r for r in sh.worksheet("Prenumeranter").get_all_records() if r["Status"] == "active"])
    arts = len(sh.worksheet("Artiklar").get_all_records())
    return render_template("admin.html", subs=subs, arts=arts)

@app.route("/admin/panel/send-digest", methods=["POST"])
@admin_required
def admin_send_digest():
    from util_email import send_digest
    days = int(request.form.get("days", 1))
    max_articles = int(request.form.get("max_articles", 6))
    Thread(target=lambda: send_digest(days=days, max_articles=max_articles)).start()
    return redirect("/admin/panel")

@app.route("/admin/panel/fetch", methods=["POST"])
@admin_required
def admin_fetch():
    Thread(target=lambda: import_module("rss_ai").fetch_and_summarize(), daemon=True).start()
    return redirect("/admin/panel")

@app.route("/admin/panel/trigger-action", methods=["POST"])
@admin_required
def trigger_action():
    workflow = request.form.get("workflow", "fetch.yml")
    trigger_github_action(workflow)
    return redirect("/admin/panel")

@app.route("/")
def index():
    return "AI-Nyheter API v2.7", 200

if __name__ == "__main__":
    app.run(debug=True, port=5000)
