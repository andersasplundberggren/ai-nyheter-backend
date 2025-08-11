# app.py – AI-Nyheter (stabil grund, Sheet som källa)
import os, sys
from functools import wraps
from threading import Thread

from flask import Flask, render_template, request, redirect, session, jsonify
from flask_cors import CORS
import gspread
from google.oauth2.service_account import Credentials

# (Valfritt) e-posthjälp – kvar för framtida bruk
try:
    from util_email import gen_token, send_confirm, send_goodbye  # noqa
except Exception:
    gen_token = send_confirm = send_goodbye = None

# ────────── Konfiguration / miljö ──────────
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
CREDS_PATH     = os.getenv("GOOGLE_CREDS_PATH", "/etc/secrets/service_account.json")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
ADMIN_TOKEN    = os.getenv("ADMIN_TOKEN")  # används för header-skydd på /admin/run-fetch

if not SPREADSHEET_ID:
    print("[app] VARNING: SPREADSHEET_ID saknas!", file=sys.stderr)

# ────────── Google Sheets-klient ──────────
creds = Credentials.from_service_account_file(CREDS_PATH, scopes=SCOPES)
gc    = gspread.authorize(creds)
sh    = gc.open_by_key(SPREADSHEET_ID)

# ────────── Flask-app ──────────
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev")
# Öppna CORS för /api/* så GitHub Pages kan hämta
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ────────── Hjälpare ──────────
def admin_required_route(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("admin"):
            return redirect("/admin/panel")
        return fn(*a, **kw)
    return wrapper

# ────────── Adminpanel (enkel, valfri att använda) ──────────
@app.route("/admin/panel", methods=["GET", "POST"])
def admin_panel():
    # Inloggning via enkel form
    if request.method == "POST":
        if request.form.get("token") == ADMIN_TOKEN:
            session["admin"] = True
            return redirect("/admin/panel")
        return render_template("admin.html", authed=False, error="Fel lösenord")

    # Visa statistisk info (kräver ej inlogg för att se själva sidan – men knapparna kräver session)
    try:
        subs = sh.worksheet("Prenumeranter").get_all_records()
    except gspread.WorksheetNotFound:
        subs = []

    try:
        arts = sh.worksheet("Artiklar").get_all_records()
    except gspread.WorksheetNotFound:
        arts = []

    return render_template(
        "admin.html",
        authed=session.get("admin"),
        subs=len(subs),
        arts=len(arts),
    )

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect("/admin/panel")

# Panel-knapp som kör hämtning (session-skyddad)
@app.route("/admin/panel/fetch", methods=["POST"])
@admin_required_route
def admin_rss_fetch():
    def job():
        try:
            from rss_fetcher import fetch_and_append
            added = fetch_and_append()
            print(f"[admin] panel/fetch klart, nya artiklar: {added}", file=sys.stderr)
        except Exception as e:
            print(f"[admin] panel/fetch fel: {e}", file=sys.stderr)

    Thread(target=job, daemon=True).start()
    return redirect("/admin/panel")

# **Manuell trigger** (POST) – kan anropas av GitHub Actions / externa system
# Skicka header: X-Admin-Token: <ADMIN_TOKEN>
@app.route("/admin/run-fetch", methods=["POST"])
def run_fetch_now():
    if ADMIN_TOKEN and (request.headers.get("X-Admin-Token") != ADMIN_TOKEN):
        return jsonify({"error": "Unauthorized"}), 401

    def job():
        try:
            from rss_fetcher import fetch_and_append
            added = fetch_and_append()
            print(f"[admin] run-fetch klart, nya artiklar: {added}", file=sys.stderr)
        except Exception as e:
            print(f"[admin] run-fetch fel: {e}", file=sys.stderr)

    Thread(target=job, daemon=True).start()
    return jsonify({"ok": True, "msg": "Fetch job started"}), 202

# ────────── Publika API-endpoints (frontend hämtar härifrån ELLER direkt från Sheet) ──────────
@app.route("/api/all")
def api_all():
    """Returnerar alla artiklar (fliken 'Artiklar') som JSON."""
    try:
        arts = sh.worksheet("Artiklar").get_all_records()
    except gspread.WorksheetNotFound:
        arts = []
    return jsonify(arts)

@app.route("/api/settings")
def api_settings():
    """Returnerar kategorirader (fliken 'Inställningar') som JSON."""
    try:
        settings = sh.worksheet("Inställningar").get_all_records()
    except gspread.WorksheetNotFound:
        settings = []
    return jsonify(settings)

# (Valfritt) Prenumeration – kan lämnas eller tas bort. Låter denna vara enkel/neutral.
@app.route("/api/subscribe", methods=["POST"])
def api_subscribe():
    data = request.get_json(silent=True) or {}
    name  = (data.get("name")  or "").strip()
    email = (data.get("email") or "").strip().lower()
    cats  = data.get("categories") or []

    if not name or not email or not isinstance(cats, list) or not cats:
        return jsonify({"error": "Alla fält är obligatoriska"}), 400

    # Se till att fliken finns
    try:
        ws = sh.worksheet("Prenumeranter")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="Prenumeranter", rows=1, cols=5)
        ws.append_row(["Namn", "E-post", "Kategorier", "Status", "Token"])

    token  = gen_token(16) if gen_token else ""

    # Skriv komplett rad (Status/Token för ev. framtida e-postflöde)
    ws.append_row([name, email, ", ".join(cats), "pending", token])

    # (Valfritt) skicka bekräftelse om util_email finns & konfigurerad
    if send_confirm and token:
        try:
            send_confirm(email, token)  # implementerad i din util_email
        except Exception as e:
            print(f"[subscribe] Kunde inte skicka bekräftelse: {e}", file=sys.stderr)

    return jsonify({"ok": True})

# Hälsa/koll
@app.route("/health")
def health():
    return "OK", 200

# ────────── Kör lokalt ──────────
if __name__ == "__main__":
    app.run(debug=True, port=5000)
