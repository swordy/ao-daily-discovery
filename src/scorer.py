"""Scoring engine for BOAMP markets — multi-category, weighted 1-5 scale."""

import json
import re
from datetime import date, datetime

# ── Harington Stack (for "match stack" criterion) ──
HARINGTON_PRODUCTS = [
    "omniafactory", "pearlflow", "migrator flow", "migrator bi",
    "dev brain", "po assistant", "techlead assistant", "code migrator",
]
HARINGTON_TECH = [
    ".net", "c#", "java", "spring", "react", "angular", "python",
    "php", "typescript", "node", "micro-services", "microservices",
    "api", "rest", "graphql", "docker", "kubernetes", "ci/cd", "devops",
]
HARINGTON_EXPERTISE = [
    "audit", "urbanisation si", "architecture", "migration bi",
    "migration data", "etl", "iam", "sécurité", "cyber", "rse",
    "qa", "tests fonctionnels", "moyens de paiements", "paiement",
]
HARINGTON_DELIVERY = [
    "centre de compétences", "centre de service", "centres de services",
    "nearshore", "onshore", "forfait", "régie", "tma",
]
HARINGTON_PROFILES = [
    "business analyste", "pmo", "chef de projet", "moa",
    "product owner", "scrum master", "développeur", "architecte",
]

IDF_DEPARTMENTS = {"75", "77", "78", "91", "92", "93", "94", "95"}


def _text_from_market(market: dict) -> str:
    """Extract searchable text from market data."""
    parts = [
        market.get("objet", ""),
        " ".join(market.get("descripteur_libelle", [])),
    ]
    donnees_raw = market.get("donnees", "")
    if isinstance(donnees_raw, str) and donnees_raw:
        try:
            donnees = json.loads(donnees_raw)
            fn = donnees.get("FNSimple", {})
            if fn:
                nm = fn.get("initial", {}).get("natureMarche", {})
                parts.append(nm.get("description", ""))
                parts.append(nm.get("intitule", ""))
            ef = donnees.get("EFORMS", {})
            if ef:
                cn = ef.get("ContractNotice", {})
                pp = cn.get("cac:ProcurementProject", {})
                desc = pp.get("cbc:Description", {})
                if isinstance(desc, dict):
                    parts.append(desc.get("#text", ""))
                elif isinstance(desc, str):
                    parts.append(desc)
        except (json.JSONDecodeError, AttributeError):
            pass
    return " ".join(parts).lower()


def _extract_budget(market: dict) -> float | None:
    """Try to extract budget from market data."""
    donnees_raw = market.get("donnees", "")
    if not isinstance(donnees_raw, str) or not donnees_raw:
        return None
    try:
        donnees = json.loads(donnees_raw)
        fn = donnees.get("FNSimple", {})
        if fn:
            nm = fn.get("initial", {}).get("natureMarche", {})
            val = nm.get("valeurEstimee", {})
            if isinstance(val, dict) and "valeur" in val:
                return float(val["valeur"])
        ef = donnees.get("EFORMS", {})
        if ef:
            cn = ef.get("ContractNotice", {})
            pp = cn.get("cac:ProcurementProject", {})
            desc_text = ""
            desc = pp.get("cbc:Description", {})
            if isinstance(desc, dict):
                desc_text = desc.get("#text", "")
            elif isinstance(desc, str):
                desc_text = desc
            match = re.search(r'(\d[\d\s]*(?:\.\d+)?)\s*(?:euros?|€|eur)\s*(?:ht|hors)', desc_text, re.IGNORECASE)
            if match:
                return float(match.group(1).replace(" ", "").replace("\u00a0", ""))
    except (json.JSONDecodeError, ValueError, AttributeError):
        pass
    return None


def _count_matches(text: str, terms: list[str]) -> int:
    """Count how many terms from the list appear in text."""
    return sum(1 for t in terms if t.lower() in text)


def _best_category(text: str, categories: dict) -> tuple[str, int, list[str]]:
    """Find the best matching category for a market.

    Returns (best_category_name, best_score_1_5, all_matching_category_names).
    """
    best_cat = "Autre"
    best_score = 1
    best_hits = 0
    all_cats = []

    for cat_name, cat_data in categories.items():
        keywords = cat_data.get("keywords", [])
        hits = _count_matches(text, keywords)
        if hits >= 2:
            all_cats.append(cat_name)

        if hits >= 4:
            cat_score = 5
        elif hits >= 3:
            cat_score = 4
        elif hits >= 2:
            cat_score = 3
        elif hits >= 1:
            cat_score = 2
        else:
            cat_score = 1

        if cat_score > best_score or (cat_score == best_score and hits > best_hits):
            best_score = cat_score
            best_cat = cat_name
            best_hits = hits

    if not all_cats and best_hits >= 1:
        all_cats = [best_cat]

    return best_cat, best_score, all_cats


def score_market(market: dict, config: dict) -> dict:
    """Score a market on a 1-5 scale with multi-category relevance.

    Returns dict with: score, category, categories, category_color,
    budget, days_left, match_pct, match_detail, breakdown.
    """
    categories = config.get("categories", {})
    scoring_cfg = config.get("scoring", {})
    weights = scoring_cfg.get("weights", {})
    idf_depts = set(config.get("departments_idf", list(IDF_DEPARTMENTS)))

    text = _text_from_market(market)
    budget = _extract_budget(market)
    departments = set(market.get("code_departement", []) or [])

    # ── 1. Best-category relevance (35%) ──
    best_cat, relevance_score, matching_cats = _best_category(text, categories)
    cat_color = categories.get(best_cat, {}).get("color", "#6B7280")

    # Also consider source categories from API enrichment
    source_cats = market.get("_source_categories", [])
    for sc in source_cats:
        if sc not in matching_cats:
            matching_cats.append(sc)

    # ── 2. Stack/profiles match (15%) ──
    all_harington = HARINGTON_PRODUCTS + HARINGTON_TECH + HARINGTON_EXPERTISE + HARINGTON_DELIVERY + HARINGTON_PROFILES
    h_hits = _count_matches(text, all_harington)
    if h_hits >= 6:
        stack_score = 5
    elif h_hits >= 4:
        stack_score = 4
    elif h_hits >= 2:
        stack_score = 3
    elif h_hits >= 1:
        stack_score = 2
    else:
        stack_score = 1

    # ── 3. Deadline (20%) ──
    deadline_str = market.get("datelimitereponse", "")
    try:
        if "T" in deadline_str:
            deadline = datetime.fromisoformat(deadline_str.replace("Z", "+00:00")).date()
        else:
            deadline = date.fromisoformat(deadline_str)
        days_left = (deadline - date.today()).days
        if days_left <= 7:
            dl_score = 5
        elif days_left <= 14:
            dl_score = 4
        elif days_left <= 30:
            dl_score = 3
        elif days_left <= 45:
            dl_score = 2
        else:
            dl_score = 1
    except (ValueError, TypeError):
        dl_score = 1
        days_left = 999

    # ── 4. Budget (15%) ──
    budget_unknown_score = scoring_cfg.get("budget_unknown_score", 3)
    if budget is not None:
        if budget >= 200000:
            bud_score = 5
        elif budget >= 100000:
            bud_score = 4
        elif budget >= 50000:
            bud_score = 3
        elif budget >= 20000:
            bud_score = 2
        else:
            bud_score = 1
    else:
        bud_score = budget_unknown_score

    # ── 5. Geography (15%) ──
    if departments & idf_depts:
        geo_score = 5
    elif not departments or len(departments) > 5:
        geo_score = 4  # National
    else:
        geo_score = 2  # Regional hors IDF

    # ── Weighted total ──
    w = {
        "relevance": weights.get("relevance", 0.35),
        "stack": weights.get("stack", 0.15),
        "deadline": weights.get("deadline", 0.20),
        "budget": weights.get("budget", 0.15),
        "geo": weights.get("geo", 0.15),
    }
    total = (
        relevance_score * w["relevance"]
        + stack_score * w["stack"]
        + dl_score * w["deadline"]
        + bud_score * w["budget"]
        + geo_score * w["geo"]
    )
    final_score = round(total * 2) / 2

    # Match percentage for Harington stack
    match_pct = min(100, int((h_hits / 8) * 100))

    matched_products = [t for t in (HARINGTON_PRODUCTS + HARINGTON_TECH[:7]) if t.lower() in text]
    matched_expertise = [t for t in HARINGTON_EXPERTISE if t.lower() in text]
    matched_delivery = [t for t in HARINGTON_DELIVERY if t.lower() in text]
    matched_profiles = [t for t in HARINGTON_PROFILES if t.lower() in text]
    match_detail_parts = matched_products + matched_expertise + matched_delivery + matched_profiles
    match_detail = " · ".join([m.title() for m in match_detail_parts[:5]]) if match_detail_parts else "Pertinence générale"

    return {
        "score": final_score,
        "category": best_cat,
        "categories": matching_cats or [best_cat],
        "category_color": cat_color,
        "budget": budget,
        "days_left": days_left,
        "match_pct": match_pct,
        "match_detail": match_detail,
        "breakdown": {
            "relevance": relevance_score,
            "stack": stack_score,
            "deadline": dl_score,
            "budget": bud_score,
            "geo": geo_score,
        },
    }


def score_all_markets(markets: list[dict], config: dict) -> list[dict]:
    """Score all markets and return sorted by score descending."""
    scored = []
    for m in markets:
        result = score_market(m, config)
        scored.append({**m, **result})
    scored.sort(key=lambda x: (-x["score"], x.get("days_left", 999)))
    return scored
