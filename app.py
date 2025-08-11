# app.py – AI-Nyheter (stabil grund, Sheet som källa)
import os, sys, json
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
# Behåll fulla scopes (du skriver till arket i /api/subscribe)
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
# Stöd både JSON i env och filväg (Render brukar köra JSON i env)
CREDS_JSON     = os.getenv("GOOGLE_CREDS_JSON")  # (valfritt)
CREDS_PATH     = os.getenv("GOOGLE_CREDS_PATH", "/etc/secrets/service_account.json")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
ADMIN_TOKEN    = os.getenv("ADMIN_TOKEN")  # används för header-skydd på /admin/run-fetch
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "https://andersasplundberggren.github.io")

if not SPREADSHEET_ID:
    print("[app] VARNING: SPREADSHEET_ID saknas!", file=sys.stderr)

# ────────── Google Sheets-klient ──────────
try:
    if CREDS_JSON:
        creds = Credentials.from_service_account_info(json.loads(CREDS_JSON), scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file(CREDS_PATH, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID) if SPREADSHEET_ID else None
except Exception as e:
    print(f"[app] Fel vid init av Sheets-klient: {e}", file=sys.stderr)
    sh = None

# ────────── Flask-app ──────────
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev")

# CORS
# - /api/*: öppet (som tidigare)
# - /public/*: låst till din GitHub Pages-domän (kan ändras via FRONTEND_ORIGIN)
CORS(app, resources={
    r"/api/*": {"origins": "*"},
    r"/public/*": {"origins": FRONTEND_ORIGIN},
})

# ────────── Hjälpare ──────────
def admin_required_route(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("admin"):
            return redirect("/admin/panel")
        return fn(*a, **kw)
    return wrapper

def _sheet_rows(tab_name: str):
    """Hämta alla rader från en flik som lista av dicts."""
    if not sh:
        raise RuntimeError("Google Sheet ej initierat (saknar SPREADSHEET_ID eller creds).")
    ws = sh.worksheet(tab_name)
    return ws.get_all_records()  # [{col: val, ...}]

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
    except Exception:
        subs = []

    try:
        arts = sh.worksheet("Artiklar").get_all_records()
    except Exception:
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

# ────────── Publika API-endpoints (befintliga) ──────────
@app.route("/api/all")
def api_all():
    """Returnerar alla artiklar (fliken 'Artiklar') som JSON."""
    try:
        arts = sh.worksheet("Artiklar").get_all_records()
    except Exception:
        arts = []
    return jsonify(arts)

@app.route("/api/settings")
def api_settings():
    """Returnerar rader från fliken 'Inställningar' som JSON."""
    try:
        settings = sh.worksheet("Inställningar").get_all_records()
    except Exception:
        settings = []
    return jsonify(settings)

# ────────── NYTT: Läs-enda proxy för frontend (GitHub Pages) ──────────
# Dessa används av din frontend för att slippa publicera arket på webben.
@app.get("/public/sheet")
def public_sheet():
    """
    Ex: /public/sheet?sheet=Artiklar  eller  /public/sheet?sheet=Kategorier
    Returnerar list[dict].
    """
    tab = request.args.get("sheet", "").strip() or "Artiklar"
    try:
        rows = _sheet_rows(tab)
        return jsonify(rows)
    except gspread.WorksheetNotFound:
        return jsonify({"error": f"Fliken '{tab}' kunde inte hittas."}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.get("/public/articles")
def public_articles():
    try:
        return jsonify(_sheet_rows("Artiklar"))
    except gspread.WorksheetNotFound:
        return jsonify({"error": "Fliken 'Artiklar' saknas."}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.get("/public/categories")
def public_categories():
    # Stöd både 'Kategorier' (ny) och 'Inställningar' (gammal) som fallback
    for tab in ("Kategorier", "Inställningar"):
        try:
            return jsonify(_sheet_rows(tab))
        except gspread.WorksheetNotFound:
            continue
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return jsonify([])

# (Valfritt) Prenumeration – kan lämnas eller tas bort.
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

    token = gen_token(16) if gen_token else ""
    ws.append_row([name, email, ", ".join(cats), "pending", token])

    if send_confirm and token:
        try:
            send_confirm(email, token)
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
