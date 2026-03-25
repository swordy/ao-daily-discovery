"""Harry Veille — AO publics — Daily pipeline orchestrator."""

import sys
from datetime import date
from pathlib import Path

from src.boamp_api import fetch_all_markets, load_config
from src.scorer import score_all_markets
from src.html_report import generate_report
from src.mailer import send_report


def main() -> int:
    today = date.today().isoformat()
    output_dir = Path("reports")
    output_dir.mkdir(exist_ok=True)
    output_path = str(output_dir / f"boamp-{today}.html")

    # 0. Load config
    config = load_config()
    cat_count = len(config.get("categories", {}))
    query_count = sum(len(c.get("queries", [])) for c in config.get("categories", {}).values())
    portfolio = config.get("harington_portfolio", {})
    product_count = len(portfolio.get("products", {}))
    expertise_count = len(portfolio.get("expertises", {}))
    delivery_count = len(portfolio.get("delivery", {}))
    profil_count = len(portfolio.get("profils", {}))
    rex_count = len(portfolio.get("rex", []))
    print(f"[0/4] Config loaded: {cat_count} categories, {query_count} queries, {product_count} products, {expertise_count} expertises, {delivery_count} delivery, {profil_count} profils, {rex_count} REX")

    # 1. Fetch markets
    print("[1/4] Fetching BOAMP markets...")
    markets = fetch_all_markets(config)
    print(f"       Found {len(markets)} unique markets")

    if not markets:
        print("[WARN] No markets found. Skipping report.")
        return 0

    # 2. Score markets (with ESN filter)
    print("[2/4] Scoring markets (ESN filter + Harington match)...")
    scored, filtered_count = score_all_markets(markets, config)
    priority = [m for m in scored if m.get("score", 0) >= 4]
    high_tier = [m for m in scored if m.get("tier") == "high"]
    print(f"       {len(scored)} marches ESN retenus sur {len(markets)} ({filtered_count} hors perimetre)")
    print(f"       {len(priority)} priority (score >= 4/5), {len(high_tier)} Fit Harington")

    if not scored:
        print("[WARN] No ESN-relevant markets after filtering. Skipping report.")
        return 0

    # 3. Generate HTML report (with top-4 fallback)
    print("[3/4] Generating HTML report...")
    result = generate_report(scored, output_path, config)
    print(f"       Report saved to {result['path']}")
    print(f"       {result['priority_count']} displayed as priority (top-4 fallback)")

    # 4. Send email
    print("[4/4] Sending email...")
    try:
        send_report(result["path"], result["priority_count"], result["total_count"])
    except Exception as e:
        print(f"[WARN] Email not sent: {e}")
        print("       Check GMAIL_ADDRESS and GMAIL_APP_PASSWORD secrets.")

    print(f"\n[DONE] Pipeline complete. {result['priority_count']} priority / {result['total_count']} total markets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
