"""Scoring engine for BOAMP markets — weighted 1-5 scale."""

import json
import re
from datetime import date, datetime

# ── Harington Catalogue (never mention Paperclip) ──
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
IA_KEYWORDS = [
    "intelligence artificielle", "ia", "machine learning", "deep learning",
    "llm", "chatbot", "nlp", "traitement du langage", "générative",
    "genai", "gpt", "data science", "modèle", "algorithme", "agentique",
    "orchestration", "rag", "fine-tuning", "prompt",
]

IDF_DEPARTMENTS = {"75", "77", "78", "91", "92", "93", "94", "95"}


def _text_from_market(market: dict) -> str:
    """Extract searchable text from market data."""
    parts = [
        market.get("objet", ""),
        " ".join(market.get("descripteur_libelle", [])),
    ]
    # Try to get description from donnees
    donnees_raw = market.get("donnees", "")
    if isinstance(donnees_raw, str) and donnees_raw:
        try:
            donnees = json.loads(donnees_raw)
            # FNSimple format
            fn = donnees.get("FNSimple", {})
            if fn:
                nm = fn.get("initial", {}).get("natureMarche", {})
                parts.append(nm.get("description", ""))
                parts.append(nm.get("intitule", ""))
            # EFORMS format
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
        # FNSimple
        fn = donnees.get("FNSimple", {})
        if fn:
            nm = fn.get("initial", {}).get("natureMarche", {})
            val = nm.get("valeurEstimee", {})
            if isinstance(val, dict) and "valeur" in val:
                return float(val["valeur"])
        # EFORMS
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
            # Search for budget in description
            match = re.search(r'(\d[\d\s]*(?:\.\d+)?)\s*(?:euros?|€|eur)\s*(?:ht|hors)', desc_text, re.IGNORECASE)
            if match:
                return float(match.group(1).replace(" ", "").replace("\u00a0", ""))
    except (json.JSONDecodeError, ValueError, AttributeError):
        pass
    return None


def _count_matches(text: str, terms: list[str]) -> int:
    """Count how many terms from the list appear in text."""
    return sum(1 for t in terms if t.lower() in text)


def score_market(market: dict) -> dict:
    """Score a market on a 1-5 scale with weighted criteria.

    Returns dict with: score, breakdown, budget, match_products, match_detail.
    """
    text = _text_from_market(market)
    budget = _extract_budget(market)
    departments = set(market.get("code_departement", []) or [])

    # ── 1. Pertinence IA/agentique (40%) ──
    ia_hits = _count_matches(text, IA_KEYWORDS)
    if ia_hits >= 5:
        ia_score = 5
    elif ia_hits >= 3:
        ia_score = 4
    elif ia_hits >= 2:
        ia_score = 3
    elif ia_hits >= 1:
        ia_score = 2
    else:
        ia_score = 1

    # ── 2. Deadline < 30j (20%) ──
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

    # ── 3. Budget > 50k€ (15%) ──
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
        bud_score = 2  # Unknown = neutral

    # ── 4. Zone IDF/national (15%) ──
    if departments & IDF_DEPARTMENTS:
        geo_score = 5
    elif not departments or len(departments) > 5:
        geo_score = 4  # National
    else:
        geo_score = 2  # Regional hors IDF

    # ── 5. Match stack Harington (10%) ──
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

    # Matched products for display
    matched_products = [t for t in (HARINGTON_PRODUCTS + HARINGTON_TECH[:7]) if t.lower() in text]
    matched_expertise = [t for t in HARINGTON_EXPERTISE if t.lower() in text]
    matched_delivery = [t for t in HARINGTON_DELIVERY if t.lower() in text]
    matched_profiles = [t for t in HARINGTON_PROFILES if t.lower() in text]

    # ── Weighted total ──
    total = (
        ia_score * 0.40
        + dl_score * 0.20
        + bud_score * 0.15
        + geo_score * 0.15
        + stack_score * 0.10
    )
    # Round to nearest 0.5
    final_score = round(total * 2) / 2

    # Match percentage for Harington stack
    match_pct = min(100, int((h_hits / 8) * 100))

    match_detail_parts = matched_products + matched_expertise + matched_delivery + matched_profiles
    match_detail = " · ".join([m.title() for m in match_detail_parts[:5]]) if match_detail_parts else "Pertinence générale"

    return {
        "score": final_score,
        "budget": budget,
        "days_left": days_left,
        "match_pct": match_pct,
        "match_detail": match_detail,
        "breakdown": {
            "ia": ia_score,
            "deadline": dl_score,
            "budget": bud_score,
            "geo": geo_score,
            "stack": stack_score,
        },
    }


def score_all_markets(markets: list[dict]) -> list[dict]:
    """Score all markets and return sorted by score descending."""
    scored = []
    for m in markets:
        result = score_market(m)
        scored.append({**m, **result})
    scored.sort(key=lambda x: (-x["score"], x.get("days_left", 999)))
    return scored
