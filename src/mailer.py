"""Email sender via Resend API."""

import base64
import os
from datetime import date
from pathlib import Path

import requests

RESEND_API_URL = "https://api.resend.com/emails"
RECIPIENT = "smeddeb@harington.fr"

MONTHS_FR = {1: "janvier", 2: "février", 3: "mars", 4: "avril", 5: "mai", 6: "juin",
             7: "juillet", 8: "août", 9: "septembre", 10: "octobre", 11: "novembre", 12: "décembre"}


def send_report(
    html_path: str,
    priority_count: int,
    total_count: int,
) -> None:
    """Send the HTML report via Resend API."""
    api_key = os.environ.get("RESEND_API_KEY")
    sender = os.environ.get("RESEND_FROM", "BOAMP Watch <onboarding@resend.dev>")
    if not api_key:
        raise RuntimeError("RESEND_API_KEY must be set")

    today = date.today()
    date_str = f"{today.day} {MONTHS_FR[today.month]} {today.year}"
    subject = f"[Harington] Veille BOAMP IA — {date_str} · {priority_count} opportunités prioritaires"

    body = (
        f"Bonjour,\n\n"
        f"Veuillez trouver en pièce jointe le rapport de veille BOAMP du {date_str}.\n\n"
        f"Résumé :\n"
        f"- {priority_count} marchés prioritaires identifiés (score >= 4/5)\n"
        f"- {total_count} marchés analysés au total\n\n"
        f"Bonne journée,\n"
        f"Harington IA Watch"
    )

    # Build payload
    payload = {
        "from": sender,
        "to": [RECIPIENT],
        "subject": subject,
        "text": body,
    }

    # Attach HTML file
    html_file = Path(html_path)
    if html_file.exists():
        content_b64 = base64.b64encode(html_file.read_bytes()).decode("utf-8")
        payload["attachments"] = [
            {
                "filename": html_file.name,
                "content": content_b64,
                "type": "text/html",
            }
        ]

    resp = requests.post(
        RESEND_API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )

    if resp.status_code == 200:
        print(f"[OK] Email sent to {RECIPIENT} via Resend")
    else:
        print(f"[ERROR] Resend API returned {resp.status_code}: {resp.text}")
        resp.raise_for_status()
