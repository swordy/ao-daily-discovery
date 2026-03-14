"""HTML report generator using Jinja2 templates."""

import json
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
IDF_DEPARTMENTS = {"75", "77", "78", "91", "92", "93", "94", "95"}

DAYS_FR = {0: "lundi", 1: "mardi", 2: "mercredi", 3: "jeudi", 4: "vendredi", 5: "samedi", 6: "dimanche"}
MONTHS_FR = {1: "janvier", 2: "février", 3: "mars", 4: "avril", 5: "mai", 6: "juin",
             7: "juillet", 8: "août", 9: "septembre", 10: "octobre", 11: "novembre", 12: "décembre"}

MIN_PRIORITY = 4  # Minimum number of markets in priority section


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


def _build_harington_tags(market: dict) -> list[dict]:
    """Build Harington-specific tags (products, REX, tech) instead of BOAMP generic tags."""
    tags = []
    for p in market.get("matched_products", [])[:2]:
        tags.append({"label": p["label"], "type": "product"})
    for r in market.get("matched_rex", [])[:2]:
        tags.append({"label": r["label"], "type": "rex"})
    for t in market.get("matched_tech", [])[:3]:
        tags.append({"label": t.title(), "type": "tech"})
    # Fallback: BOAMP descriptors if no Harington match
    if not tags:
        descs = market.get("descripteur_libelle", [])
        for d in descs[:3]:
            tags.append({"label": d, "type": "generic"})
    return tags[:6]


def _enrich_market(market: dict) -> dict:
    """Add display-ready fields to a scored market."""
    return {
        **market,
        "deadline_display": _format_deadline(market.get("datelimitereponse", "")),
        "budget_display": _format_budget(market.get("budget")),
        "description_short": _extract_description(market),
        "harington_tags": _build_harington_tags(market),
        "geo_label": market.get("geo_label", ""),
        "relevance_summary": market.get("relevance_summary", []),
        "tier": market.get("tier", "low"),
        "ao_type": market.get("ao_type", ""),
    }


def generate_report(scored_markets: list[dict], output_path: str, config: dict | None = None) -> dict:
    """Generate HTML report from scored markets.

    Returns dict with: path, priority_count, total_count.
    """
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)
    template = env.get_template("report.html")

    today = date.today()
    enriched = [_enrich_market(m) for m in scored_markets]

    # Priority: score >= 4, with TOP 4 fallback
    priority = [m for m in enriched if m.get("score", 0) >= 4]
    if len(priority) < MIN_PRIORITY:
        remaining = sorted(
            [m for m in enriched if m not in priority],
            key=lambda x: (-x["score"], x.get("days_left", 999)),
        )
        for m in remaining[:MIN_PRIORITY - len(priority)]:
            m["promoted"] = True
            priority.append(m)

    # Others: everything not in priority, score >= 1.5
    priority_ids = {m.get("idweb") for m in priority}
    others = [m for m in enriched if m.get("idweb") not in priority_ids and m.get("score", 0) >= 1.5]

    urgent_15j = sum(1 for m in enriched if m.get("days_left", 999) <= 15)
    urgent_30j = sum(1 for m in enriched if m.get("days_left", 999) <= 30)

    # Category filters with counts
    categories_config = config.get("categories", {}) if config else {}
    cat_counts = Counter(m.get("category", "Autre") for m in enriched)
    category_filters = []
    for cat_name, cat_data in categories_config.items():
        count = cat_counts.get(cat_name, 0)
        if count > 0:
            category_filters.append({
                "name": cat_name,
                "label": cat_data.get("label", cat_name),
                "color": cat_data.get("color", "#6B7280"),
                "count": count,
            })
    category_filters.sort(key=lambda x: -x["count"])

    # Relevance data for popup (keyed by idweb)
    relevance_data = {}
    for m in enriched:
        idweb = m.get("idweb", "")
        if idweb and m.get("relevance_summary"):
            relevance_data[idweb] = {
                "title": (m.get("objet", ""))[:80],
                "bullets": m.get("relevance_summary", []),
                "match_pct": m.get("match_pct", 0),
                "tier": m.get("tier", "low"),
                "ao_type": m.get("ao_type", ""),
            }

    html = template.render(
        report_date=today.isoformat(),
        report_date_fr=_date_fr(today),
        total_markets=len(enriched),
        priority_count=len(priority),
        urgent_15j=urgent_15j,
        urgent_30j=urgent_30j,
        priority_markets=priority,
        other_markets=others,
        category_filters=category_filters,
        relevance_data=json.dumps(relevance_data, ensure_ascii=False),
    )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(html, encoding="utf-8")
    return {
        "path": output_path,
        "priority_count": len(priority),
        "total_count": len(enriched),
    }
