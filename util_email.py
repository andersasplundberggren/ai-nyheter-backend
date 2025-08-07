# util_email.py
import os, requests, random, string, hmac, hashlib

MAILJET_API   = os.getenv("MAILJET_API_KEY")
MAILJET_SECRET= os.getenv("MAILJET_API_SECRET")
SENDER        = "AI-Nyheter <no-reply@dindomän.se>"

def _send_mail(to, subject, html):
    url = "https://api.mailjet.com/v3.1/send"
    resp = requests.post(
        url,
        auth=(MAILJET_API, MAILJET_SECRET),
        json={
          "Messages": [{
            "From":   {"Email": SENDER.split()[-1].strip("<>"), "Name": "AI-Nyheter"},
            "To":     [{"Email": to}],
            "Subject": subject,
            "HTMLPart": html
          }]
        }
    )
    resp.raise_for_status()

def gen_token():
    return hashlib.sha1(os.urandom(32)).hexdigest()

def send_confirm(to, token):
    url = f"https://ai-nyheter-backend.onrender.com/api/confirm?email={to}&tok={token}"
    html = f"""
    <p>Klicka för att bekräfta din prenumeration:</p>
    <p><a href="{url}">Aktivera AI-Nyheter</a></p>
    """
    _send_mail(to, "Bekräfta din prenumeration", html)

def send_goodbye(to):
    html = "<p>Din prenumeration har avslutats. Välkommen tillbaka när du vill!</p>"
    _send_mail(to, "Prenumerationen avslutad", html)
