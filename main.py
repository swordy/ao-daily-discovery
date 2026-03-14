"""BOAMP IA Watch — Daily pipeline orchestrator."""

import sys
from datetime import date
from pathlib import Path

from src.boamp_api import fetch_all_markets
from src.scorer import score_all_markets
from src.html_report import generate_report
from src.mailer import send_report


def main() -> int:
    today = date.today().isoformat()
    output_dir = Path("reports")
    output_dir.mkdir(exist_ok=True)
    output_path = str(output_dir / f"boamp-{today}.html")

    # 1. Fetch markets
    print(f"[1/4] Fetching BOAMP markets...")
    markets = fetch_all_markets()
    print(f"       Found {len(markets)} unique markets")

    if not markets:
        print("[WARN] No markets found. Skipping report.")
        return 0

    # 2. Score markets
    print(f"[2/4] Scoring markets...")
    scored = score_all_markets(markets)
    priority = [m for m in scored if m.get("score", 0) >= 4]
    print(f"       {len(priority)} priority markets (score >= 4/5)")

    # 3. Generate HTML report
    print(f"[3/4] Generating HTML report...")
    report_path = generate_report(scored, output_path)
    print(f"       Report saved to {report_path}")

    # 4. Send email
    print(f"[4/4] Sending email...")
    try:
        send_report(report_path, len(priority), len(scored))
    except Exception as e:
        print(f"[WARN] Email not sent: {e}")
        print("       Check RESEND_API_KEY and RESEND_FROM secrets.")

    print(f"\n[DONE] Pipeline complete. {len(priority)} priority / {len(scored)} total markets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
