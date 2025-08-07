# util_email.py
"""
Hanterar e-post för AI-Nyheter
• gen_token()   – skapar slumpmässig token (dubbel opt-in & avlänkning)
• send_confirm  – skickar bekräftelsemejl
• send_goodbye  – kort bekräftelse vid avanmälan
• send_digest   – sammanställer dagens nyhetsbrev och skickar via Mailjet
"""

import os, secrets, datetime, sys
import mailjet_rest
from flask import render_template
from app import app, sh   # återanvänder Flask-app & Google-Sheet-objekt
from news_db import latest

# ────────── Konstanta miljö-variabler ──────────
MJ_KEY    = os.getenv("MAILJET_API_KEY")
MJ_SECRET = os.getenv("MAILJET_API_SECRET")
SENDER    = os.getenv("SENDER_EMAIL", "nyheter@example.com")

# Mailjet-klient
mj = mailjet_rest.Client(auth=(MJ_KEY, MJ_SECRET), version='v3.1')

# -------------------------------------------------------------------------

def gen_token(n: int = 24) -> str:
    return secrets.token_urlsafe(n)

# -------------------------------------------------------------------------


def _send(subject: str, html: str, to_addr: str):
    """
    Liten hjälpfunktion som skickar ett (HTML)-mejl via Mailjet.
    """
    if not (MJ_KEY and MJ_SECRET):
        print("[email] Mailjet-nycklar saknas", file=sys.stderr)
        return False

    data = {
        "Messages": [
            {
                "From":     {"Email": SENDER, "Name": "AI-Nyheter"},
                "To":       [{"Email": to_addr}],
                "Subject":  subject,
                "HTMLPart": html,
            }
        ]
    }
    res = mj.send.create(data=data)
    ok  = res.status_code == 200
    if not ok:
        print("[email] Mailjet fel:", res.status_code, res.json(), file=sys.stderr)
    return ok


# -------------------------------------------------------------------------
# 1) Bekräftelse-mejl
# -------------------------------------------------------------------------

def send_confirm(email: str, token: str):
    confirm_link = f"https://ai-nyheter-backend.onrender.com/api/confirm?email={email}&tok={token}"
    html = f"""
    <p>Hej!</p>
    <p>Tack för att du vill prenumerera på AI-Nyheter. Klicka på knappen
       nedan för att bekräfta din adress.</p>
    <p><a href="{confirm_link}"
          style="background:#6366f1;color:#fff;padding:10px 18px;
                 text-decoration:none;border-radius:6px;">Bekräfta</a></p>
    <p>Ignorera detta mejl om du inte anmält dig.</p>
    """
    _send("Bekräfta din prenumeration på AI-Nyheter", html, email)


# -------------------------------------------------------------------------
# 2) Avslutsmejl
# -------------------------------------------------------------------------

def send_goodbye(email: str):
    html = "<p>Din prenumeration på AI-Nyheter är nu avslutad.</p>"
    _send("Prenumerationen avslutad – AI-Nyheter", html, email)


# -------------------------------------------------------------------------
# 3) Dagligt nyhetsbrev
# -------------------------------------------------------------------------

def send_digest(dryrun: bool = False, test_to: str | None = None):
    """
    • Hämtar senaste 24 h (eller senaste X artiklar)
    • Grupperar per prenumerant → deras valda kategorier
    • Renderar templates/digest.html
    • Skickar via Mailjet

    Vid dryrun=True returnerar den bara hur många mejl som *skulle* skicka.
    """
    # 1. Hämta artiklar (senaste 24 h eller max 40 poster)
    today   = datetime.date.today().isoformat()
    arts    = [a for a in latest(40) if a["date"] >= today]

    if not arts:
        print("[digest] Inga nya artiklar – hoppar utskick", file=sys.stderr)
        return 0

    # 2. Ladda prenumeranter
    subs = sh.worksheet("Prenumeranter").get_all_records()

    # 3. För varje prenumerant → filtrera på deras kategorier
    mail_count = 0
    for sub in subs:
        if sub.get("Status") != "active":
            continue

        cats = [c.strip() for c in sub["Kategorier"].split(",")] if sub["Kategorier"] != "ALL" else []
        filtered = [a for a in arts if (not cats or a["category"] in cats)]

        if not filtered:
            continue

        unsub_link = (
            "https://ai-nyheter-backend.onrender.com/api/unsubscribe"
            f"?email={sub['E-post']}&tok={sub['Token']}"
        )

        # 4. Rendera HTML-mall (Flask-jinja2)
        html_body = render_template(
            "digest.html",
            date=datetime.date.today().strftime("%Y-%m-%d"),
            articles=filtered,
            unsubscribe_link=unsub_link,
        )

        # 5. Skicka eller torr-kör
        recipient = test_to or sub["E-post"]
        if not dryrun:
            ok = _send("AI-Nyheter – Dagens sammanfattning", html_body, recipient)
            if ok:
                mail_count += 1
        else:
            mail_count += 1   # räkna ändå

        if test_to:   # om vi kör test → bara ett mejl
            break

    print(f"[digest] {mail_count} brev {'skickade' if not dryrun else 'att skicka'}",
          file=sys.stderr)
    return mail_count
