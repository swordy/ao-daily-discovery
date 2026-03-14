"""Email sender via Gmail SMTP (App Password) — multi-recipient support."""

import os
import smtplib
from datetime import date
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
DEFAULT_RECIPIENT = "smeddeb@harington.fr"

MONTHS_FR = {1: "janvier", 2: "février", 3: "mars", 4: "avril", 5: "mai", 6: "juin",
             7: "juillet", 8: "août", 9: "septembre", 10: "octobre", 11: "novembre", 12: "décembre"}


def _get_recipients() -> list[str]:
    """Get recipient list from RECIPIENTS env var (comma-separated) or default."""
    raw = os.environ.get("RECIPIENTS", "").strip()
    if raw:
        return [r.strip() for r in raw.split(",") if r.strip()]
    return [DEFAULT_RECIPIENT]


def send_report(
    html_path: str,
    priority_count: int,
    total_count: int,
) -> None:
    """Send the HTML report via Gmail SMTP with App Password."""
    sender = os.environ.get("GMAIL_ADDRESS")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    if not sender or not app_password:
        raise RuntimeError("GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set")

    recipients = _get_recipients()

    today = date.today()
    date_str = f"{today.day} {MONTHS_FR[today.month]} {today.year}"
    subject = f"[Harington] Veille BOAMP IA — {date_str} · {priority_count} opportunités prioritaires"

    msg = MIMEMultipart()
    msg["From"] = f"BOAMP Watch <{sender}>"
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject

    body = (
        f"Bonjour,\n\n"
        f"Veuillez trouver en pièce jointe le rapport de veille BOAMP du {date_str}.\n\n"
        f"Résumé :\n"
        f"- {priority_count} marchés prioritaires identifiés (score >= 4/5)\n"
        f"- {total_count} marchés analysés au total\n\n"
        f"Bonne journée,\n"
        f"Harington IA Watch"
    )
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # Attach HTML file
    html_file = Path(html_path)
    if html_file.exists():
        part = MIMEBase("text", "html")
        part.set_payload(html_file.read_bytes())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={html_file.name}")
        msg.attach(part)

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(sender, app_password)
        server.send_message(msg)

    print(f"[OK] Email sent to {', '.join(recipients)} via Gmail SMTP")
