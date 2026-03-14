"""HTML report generator using Jinja2 templates."""

import json
import re
from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
IDF_DEPARTMENTS = {"75", "77", "78", "91", "92", "93", "94", "95"}

DAYS_FR = {0: "lundi", 1: "mardi", 2: "mercredi", 3: "jeudi", 4: "vendredi", 5: "samedi", 6: "dimanche"}
MONTHS_FR = {1: "janvier", 2: "février", 3: "mars", 4: "avril", 5: "mai", 6: "juin",
             7: "juillet", 8: "août", 9: "septembre", 10: "octobre", 11: "novembre", 12: "décembre"}


def _date_fr(d: date) -> str:
    return f"{DAYS_FR[d.weekday()]} {d.day} {MONTHS_FR[d.month]} {d.year}"


def _format_deadline(deadline_str: str) -> str:
    try:
        if "T" in deadline_str:
            dt = datetime.fromisoformat(deadline_str.replace("Z", "+00:00"))
            return f"{dt.day} {MONTHS_FR[dt.month]} {dt.year}"
        d = date.fromisoformat(deadline_str)
        return f"{d.day} {MONTHS_FR[d.month]} {d.year}"
    except (ValueError, TypeError):
        return "NC"


def _format_budget(budget: float | None) -> str:
    if budget is None:
        return ""
    if budget >= 1_000_000:
        return f"{budget / 1_000_000:.1f}M€"
    if budget >= 1000:
        return f"{int(budget / 1000)}k€"
    return f"{int(budget)}€"


def _extract_description(market: dict) -> str:
    """Get a short description from market data."""
    donnees_raw = market.get("donnees", "")
    if not isinstance(donnees_raw, str):
        return market.get("objet", "")
    try:
        donnees = json.loads(donnees_raw)
        fn = donnees.get("FNSimple", {})
        if fn:
            desc = fn.get("initial", {}).get("natureMarche", {}).get("description", "")
            if desc:
                return desc[:200]
        ef = donnees.get("EFORMS", {})
        if ef:
            cn = ef.get("ContractNotice", {})
            pp = cn.get("cac:ProcurementProject", {})
            d = pp.get("cbc:Description", {})
            text = d.get("#text", "") if isinstance(d, dict) else str(d)
            if text:
                return text[:200]
    except (json.JSONDecodeError, AttributeError):
        pass
    return market.get("objet", "")


def _extract_meta_tags(market: dict) -> list[str]:
    """Generate meta tags from market data."""
    tags = []
    descs = market.get("descripteur_libelle", [])
    if descs:
        tags.extend(descs[:2])
    famille = market.get("famille_libelle", "")
    if famille:
        tags.append(famille)
    proc = market.get("procedure_libelle", "")
    if proc:
        tags.append(proc)
    return tags[:4]


def _geo_label(departments: list[str]) -> str:
    if not departments:
        return "National"
    dept_set = set(departments)
    if dept_set & IDF_DEPARTMENTS:
        return "Île-de-France"
    if len(departments) > 3:
        return "National"
    return f"Dept. {', '.join(departments)}"


def _enrich_market(market: dict) -> dict:
    """Add display-ready fields to a scored market."""
    departments = market.get("code_departement", []) or []
    dept_set = set(departments)
    return {
        **market,
        "deadline_display": _format_deadline(market.get("datelimitereponse", "")),
        "budget_display": _format_budget(market.get("budget")),
        "description_short": _extract_description(market),
        "meta_tags": _extract_meta_tags(market),
        "is_idf": bool(dept_set & IDF_DEPARTMENTS),
        "geo_label": _geo_label(departments),
    }


def generate_report(scored_markets: list[dict], output_path: str) -> str:
    """Generate HTML report from scored markets.

    Returns the output file path.
    """
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)
    template = env.get_template("report.html")

    today = date.today()
    enriched = [_enrich_market(m) for m in scored_markets]

    priority = [m for m in enriched if m.get("score", 0) >= 4]
    others = [m for m in enriched if m.get("score", 0) < 4 and m.get("score", 0) >= 2]

    urgent = sum(1 for m in enriched if m.get("days_left", 999) <= 7)

    budgets = [m.get("budget", 0) or 0 for m in enriched if m.get("budget")]
    total_budget = sum(budgets)

    html = template.render(
        report_date=today.isoformat(),
        report_date_fr=_date_fr(today),
        total_markets=len(enriched),
        priority_count=len(priority),
        urgent_count=urgent,
        total_budget_display=_format_budget(total_budget) if total_budget else "NC",
        priority_markets=priority,
        other_markets=others,
    )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(html, encoding="utf-8")
    return output_path
