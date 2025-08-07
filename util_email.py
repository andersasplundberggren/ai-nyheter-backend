# util_email.py
"""
E-post-hjälp för AI-Nyheter
──────────────────────────
• gen_token()          – slumpad token (dubbel opt-in / avanmälan)
• send_confirm()       – bekräftelsemejl
• send_goodbye()       – bekräftar avanmälan
• send_digest()        – dagligt/veckovis nyhetsbrev via Mailjet
"""

from __future__ import annotations
import os, secrets, datetime, sys, typing as _t

import mailjet_rest
from flask import render_template
from news_db import latest


# ────────── Mailjet-konfiguration ──────────
MJ_KEY    = os.getenv("MAILJET_API_KEY")
MJ_SECRET = os.getenv("MAILJET_API_SECRET")
SENDER    = os.getenv("SENDER_EMAIL", "nyheter@example.com")

mj = mailjet_rest.Client(auth=(MJ_KEY, MJ_SECRET), version="v3.1")


# ────────── Små hjälpare ──────────
def gen_token(n: int = 24) -> str:
    return secrets.token_urlsafe(n)


def _send(subject: str, html: str, to_addr: str) -> bool:
    """Enkel wrapper runt Mailjet-API:t (HTML-mejl)."""
    if not (MJ_KEY and MJ_SECRET):
        print("[email] Mailjet-nycklar saknas – inget skickat", file=sys.stderr)
        return False

    data = {
        "Messages": [
            {
                "From": {"Email": SENDER, "Name": "AI-Nyheter"},
                "To":   [{"Email": to_addr}],
                "Subject":  subject,
                "HTMLPart": html,
            }
        ]
    }

    res = mj.send.create(data=data)

    # 🧪 Extra loggning av ALLA försök
    print("[email] Mailjet status:", res.status_code, file=sys.stderr)
    try:
        print("[email] Mailjet response:", res.json(), file=sys.stderr)
    except Exception as e:
        print("[email] Mailjet response parse error:", str(e), file=sys.stderr)

    if res.status_code != 200:
        return False
    return True


# ────────── 1. Bekräftelse-mejl ──────────
def send_confirm(email: str, token: str) -> None:
    link = (
        "https://ai-nyheter-backend.onrender.com/api/confirm"
        f"?email={email}&tok={token}"
    )
    html = f"""
    <p>Hej!</p>
    <p>Tack för att du vill prenumerera på AI-Nyheter.
       Klicka på knappen nedan för att bekräfta din adress.</p>
    <p><a href="{link}" style="
          background:#6366f1;color:#fff;padding:10px 18px;
          text-decoration:none;border-radius:6px;">Bekräfta</a></p>
    <p>Ignorera mejlet om du inte har anmält dig.</p>
    """
    _send("Bekräfta din prenumeration på AI-Nyheter", html, email)


# ────────── 2. Avslutsmejl ──────────
def send_goodbye(email: str) -> None:
    html = "<p>Din prenumeration på AI-Nyheter är nu avslutad.</p>"
    _send("Prenumerationen avslutad – AI-Nyheter", html, email)


# ────────── 3. Dagligt/veckovis digest ──────────
def send_digest(
    subscribers: list[dict] | None = None,
    articles:    list[dict] | None = None,
    *,
    test_to: str | None = None,
    dryrun: bool = False,
    force:  bool = False,
) -> int:
    """
    Skicka nyhetsbrev.

    • `subscribers` – lista från Google-Sheet‐fliken **Prenumeranter**
                      (om `None` hämtas den internt).
    • `articles`    – lista med artikeldikter (`latest()`-format).
                      (om `None` hämtas senaste 40 & filtreras på dagens datum)
    • `test_to`     – e-postadress att skicka EN kopia till (dry-run).
    • `dryrun`      – räkna bara hur många som skulle få brev.
    • `force`       – skicka även om `articles` är tom.

    Returnerar antalet (skickade eller “skulle skickas”).
    """

    # ── 0. Hämta data internt om det inte skickats in ──
    if articles is None:
        today = datetime.date.today().isoformat()
        articles = [a for a in latest(40) if a["date"] >= today]

    if not articles and not force:
        print("[digest] Inga nya artiklar – hoppar utskick", file=sys.stderr)
        return 0

    if subscribers is None:
        # Lazy-import för att undvika cirkelberoende
        from app import sh  # noqa: WPS433
        subscribers = sh.worksheet("Prenumeranter").get_all_records()

    # ── 1. Loopa igenom prenumeranter ──
    sent = 0
    for sub in subscribers:
        if sub.get("Status") != "active":
            continue

        if sub["Kategorier"] == "ALL" or not sub["Kategorier"].strip():
            wanted = None   # => alla kategorier
        else:
            wanted = [c.strip() for c in sub["Kategorier"].split(",")]

        user_articles = [
            a for a in articles if (wanted is None or a["category"] in wanted)
        ]
        if not user_articles and not force:
            continue

        unsub = (
            "https://ai-nyheter-backend.onrender.com/api/unsubscribe"
            f"?email={sub['E-post']}&tok={sub['Token']}"
        )

        html_body = render_template(
            "digest.html",
            date=datetime.date.today().strftime("%Y-%m-%d"),
            articles=user_articles,
            unsubscribe_link=unsub,
        )

        recipient = test_to or sub["E-post"]

        if not dryrun:
            if _send("AI-Nyheter – Dagens sammanfattning", html_body, recipient):
                sent += 1
        else:
            sent += 1

        if test_to:
            break

    print(f"[digest] {sent} brev {'skickade' if not dryrun else 'att skicka'}",
          file=sys.stderr)
    return sent
