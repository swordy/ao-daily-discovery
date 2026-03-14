"""Scoring engine for BOAMP markets — Harington-specific, portfolio-driven."""

import json
import re
from datetime import date, datetime


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


# ── ESN PRE-FILTER ──

def _is_esn_market(text: str, esn_config: dict) -> bool:
    """Pre-filter: only keep markets relevant for an ESN (IT services)."""
    if not esn_config.get("enabled", True):
        return True

    # Check exclusion keywords
    for excl in esn_config.get("exclusion_keywords", []):
        if excl.lower() in text:
            return False

    # Check inclusion keywords (at least min_matches)
    inclusions = esn_config.get("inclusion_required", [])
    min_matches = esn_config.get("min_inclusion_matches", 1)
    hits = sum(1 for inc in inclusions if inc.lower() in text)
    return hits >= min_matches


# ── CATEGORY ASSIGNMENT (for display/filter) ──

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
        hits = sum(1 for kw in keywords if kw.lower() in text)
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


# ── HARINGTON MATCH SUB-CRITERIA ──

def _score_tech_stack(text: str, tech_stack: dict) -> tuple[float, list[str]]:
    """Score tech stack match. Returns (score_1_5, matched_terms)."""
    all_techs = []
    for category_terms in tech_stack.values():
        all_techs.extend(category_terms)

    matched = []
    seen = set()
    for t in all_techs:
        tl = t.lower()
        if tl in text and tl not in seen:
            matched.append(t)
            seen.add(tl)

    count = len(matched)
    # Thresholds adapted for BOAMP: AOs rarely mention specific tech
    if count >= 6:
        score = 5
    elif count >= 4:
        score = 4
    elif count >= 2:
        score = 3
    elif count >= 1:
        score = 2
    else:
        score = 1

    return score, matched


def _score_product_match(text: str, products: dict) -> tuple[float, list[dict]]:
    """Score Harington product match. Returns (score_1_5, matched_products_info)."""
    matched_products = []

    for product_name, product_data in products.items():
        product_keywords = product_data.get("keywords", [])
        hits = sum(1 for kw in product_keywords if kw.lower() in text)
        # Strong match: 2+ keywords → full confidence
        # Partial match: 1 keyword → lower confidence but still counts
        if hits >= 2:
            matched_products.append({
                "name": product_name,
                "label": product_data.get("label", product_name),
                "description": product_data.get("description", ""),
                "hits": hits,
                "confidence": round(min(hits / max(len(product_keywords), 1), 1.0), 2),
            })
        elif hits == 1 and len(product_keywords) <= 5:
            matched_products.append({
                "name": product_name,
                "label": product_data.get("label", product_name),
                "description": product_data.get("description", ""),
                "hits": hits,
                "confidence": round(1 / max(len(product_keywords), 1), 2),
            })

    matched_products.sort(key=lambda x: -x["confidence"])

    # Score based on number and quality of matches
    strong_matches = [p for p in matched_products if p["hits"] >= 2]
    if len(strong_matches) >= 3:
        score = 5
    elif len(strong_matches) >= 2:
        score = 4
    elif len(strong_matches) >= 1:
        score = 3
    elif len(matched_products) >= 2:
        score = 3
    elif len(matched_products) >= 1:
        score = 2
    else:
        # Fallback: check general IA/agentique vocabulary
        ia_terms = ["agentique", "agent ia", "rag", "langchain", "ia générative",
                     "intelligence artificielle", "llm", "machine learning"]
        ia_hits = sum(1 for t in ia_terms if t in text)
        if ia_hits >= 2:
            score = 2
        else:
            score = 1

    return score, matched_products


def _score_rex_sector(text: str, rex_list: list[dict], sectors: list[str]) -> tuple[float, list[dict]]:
    """Score REX and sector match. Returns (score_1_5, matched_rex_info)."""
    matched_rex = []

    for rex in rex_list:
        rex_keywords = rex.get("keywords", [])
        rex_stack = rex.get("stack", [])

        kw_hits = sum(1 for kw in rex_keywords if kw.lower() in text)
        stack_hits = sum(1 for s in rex_stack if s.lower() in text)
        total_hits = kw_hits + stack_hits
        total_possible = len(rex_keywords) + len(rex_stack)

        if total_hits >= 2:
            matched_rex.append({
                "id": rex["id"],
                "label": rex["label"],
                "sector": rex["sector"],
                "duration": rex["duration"],
                "confidence": round(total_hits / max(total_possible, 1), 2),
            })

    matched_rex.sort(key=lambda x: -x["confidence"])

    # Also check sector keywords
    sector_hits = sum(1 for s in sectors if s.lower() in text)

    if len(matched_rex) >= 2 and sector_hits >= 1:
        score = 5
    elif len(matched_rex) >= 1 and sector_hits >= 1:
        score = 4
    elif len(matched_rex) >= 1:
        score = 3
    elif sector_hits >= 2:
        score = 3
    elif sector_hits >= 1:
        score = 2
    else:
        score = 1

    return score, matched_rex


# ── AO LEGITIMACY ──

def _score_ao_legitimacy(text: str, service_types: dict) -> tuple[float, str, int]:
    """Score AO type legitimacy. Returns (score_1_5, best_type_label, legitimacy_raw)."""
    best_type = "Autre"
    best_legitimacy = 1
    best_hits = 0

    for type_name, type_data in service_types.items():
        type_keywords = type_data.get("keywords", [])
        legitimacy = type_data.get("legitimacy", 1)
        hits = sum(1 for kw in type_keywords if kw.lower() in text)

        if hits >= 1 and (legitimacy > best_legitimacy or
                          (legitimacy == best_legitimacy and hits > best_hits)):
            best_type = type_name
            best_legitimacy = legitimacy
            best_hits = hits

    return float(best_legitimacy), best_type, best_legitimacy


# ── DEADLINE & BUDGET (kept from previous) ──

def _score_deadline(deadline_str: str) -> tuple[float, int]:
    """Score deadline urgency. Returns (score_1_5, days_left)."""
    try:
        if "T" in deadline_str:
            deadline = datetime.fromisoformat(deadline_str.replace("Z", "+00:00")).date()
        else:
            deadline = date.fromisoformat(deadline_str)
        days_left = (deadline - date.today()).days
        if days_left <= 7:
            score = 5
        elif days_left <= 14:
            score = 4
        elif days_left <= 30:
            score = 3
        elif days_left <= 45:
            score = 2
        else:
            score = 1
        return float(score), days_left
    except (ValueError, TypeError):
        return 1.0, 999


def _score_budget(budget: float | None, unknown_score: int = 3) -> float:
    """Score budget attractiveness."""
    if budget is not None:
        if budget >= 200000:
            return 5
        if budget >= 100000:
            return 4
        if budget >= 50000:
            return 3
        if budget >= 20000:
            return 2
        return 1
    return float(unknown_score)


# ── RELEVANCE SUMMARY ──

def _generate_relevance_summary(
    market: dict,
    matched_products: list[dict],
    matched_rex: list[dict],
    matched_tech: list[str],
    ao_type: str,
    ao_legitimacy: int,
) -> list[str]:
    """Generate 7-10 bullet points relevance summary (deterministic, no LLM)."""
    bullets = []

    objet = market.get("objet", "")
    buyer = market.get("nomacheteur", "")

    # 1-2 bullets: understanding of AO subject
    bullets.append(f"Objet : {objet[:150]}")
    if buyer:
        bullets.append(f"Acheteur : {buyer}")

    # 1 bullet: AO type match
    if ao_legitimacy >= 4:
        bullets.append(f"Type d'AO \"{ao_type}\" : forte légitimité Harington ({ao_legitimacy}/5)")
    elif ao_legitimacy >= 3:
        bullets.append(f"Type d'AO \"{ao_type}\" : légitimité correcte ({ao_legitimacy}/5)")
    else:
        bullets.append(f"Type d'AO \"{ao_type}\" : légitimité limitée ({ao_legitimacy}/5)")

    # 1-2 bullets: tech stack matched
    if matched_tech:
        tech_display = ", ".join(t.title() for t in matched_tech[:8])
        bullets.append(f"Stack technique matchée : {tech_display}")

    # 1-2 bullets: applicable products
    if matched_products:
        for prod in matched_products[:2]:
            bullets.append(f"Produit applicable : {prod['label']} — {prod['description']}")
    else:
        bullets.append("Aucun produit interne directement applicable")

    # 1-2 bullets: REX/sector experience
    if matched_rex:
        for rex in matched_rex[:2]:
            bullets.append(f"REX sectoriel : {rex['label']} ({rex['sector']}, {rex['duration']})")
    else:
        bullets.append("Pas de REX sectoriel directement lié")

    # Pad to minimum 7 if needed
    if len(bullets) < 7:
        departments = market.get("code_departement", []) or []
        if departments:
            bullets.append(f"Géographie : département(s) {', '.join(departments)}")
    if len(bullets) < 7:
        deadline = market.get("datelimitereponse", "")
        if deadline:
            bullets.append(f"Date limite de réponse : {deadline[:10]}")
    if len(bullets) < 7:
        descripteurs = market.get("descripteur_libelle", [])
        if descripteurs:
            bullets.append(f"Descripteurs BOAMP : {', '.join(descripteurs[:4])}")

    return bullets[:10]


# ── GEO LABEL (display only, not scored) ──

IDF_DEPARTMENTS = {"75", "77", "78", "91", "92", "93", "94", "95"}


def _geo_label(departments: list, idf_depts: set | None = None) -> str:
    """Generate geographic label for display."""
    if idf_depts is None:
        idf_depts = IDF_DEPARTMENTS
    if not departments:
        return "National"
    dept_set = set(departments)
    if dept_set & idf_depts:
        return "Île-de-France"
    if len(departments) > 3:
        return "National"
    return f"Dept. {', '.join(departments)}"


# ── MAIN SCORING FUNCTION ──

def score_market(market: dict, config: dict) -> dict | None:
    """Score a market with Harington-specific matching.

    Returns None if market is filtered out (not ESN-relevant).
    """
    text = _text_from_market(market)

    # 0. ESN pre-filter
    esn_config = config.get("esn_filter", {})
    if not _is_esn_market(text, esn_config):
        return None

    portfolio = config.get("harington_portfolio", {})
    scoring_cfg = config.get("scoring", {})
    weights = scoring_cfg.get("weights", {})
    sub_weights = scoring_cfg.get("harington_match_sub_weights", {})
    categories = config.get("categories", {})
    idf_depts = set(config.get("departments_idf", list(IDF_DEPARTMENTS)))

    # Category for display/filter
    best_cat, _, matching_cats = _best_category(text, categories)
    cat_color = categories.get(best_cat, {}).get("color", "#6B7280")
    source_cats = market.get("_source_categories", [])
    for sc in source_cats:
        if sc not in matching_cats:
            matching_cats.append(sc)

    # ── 1. HARINGTON MATCH (60%) ──
    tech_score, matched_tech = _score_tech_stack(text, portfolio.get("tech_stack", {}))
    product_score, matched_products = _score_product_match(text, portfolio.get("products", {}))
    rex_score, matched_rex = _score_rex_sector(
        text, portfolio.get("rex", []), portfolio.get("sectors", [])
    )

    harington_match = (
        tech_score * sub_weights.get("tech_stack", 0.30)
        + product_score * sub_weights.get("product_match", 0.40)
        + rex_score * sub_weights.get("rex_sector", 0.30)
    )

    # ── 2. AO LEGITIMACY (30%) ──
    legitimacy_score, ao_type, ao_legitimacy_raw = _score_ao_legitimacy(
        text, portfolio.get("service_types", {})
    )

    # ── SYNERGY BONUS ──
    # When product match + high legitimacy combine, boost the harington_match
    # This rewards AOs where Harington has BOTH a product AND a legitimate AO type
    synergy_bonus = 0.0
    if product_score >= 3 and ao_legitimacy_raw >= 4:
        synergy_bonus = 0.8  # Strong synergy
    elif product_score >= 2 and ao_legitimacy_raw >= 4:
        synergy_bonus = 0.4  # Moderate synergy
    elif matched_rex and ao_legitimacy_raw >= 3:
        synergy_bonus = 0.4  # REX + legitimate AO type
    harington_match = min(5.0, harington_match + synergy_bonus)

    # ── 3. DEADLINE (10%) ──
    deadline_str = market.get("datelimitereponse", "")
    dl_score, days_left = _score_deadline(deadline_str)

    # ── 4. BUDGET (10%) ──
    budget = _extract_budget(market)
    budget_unknown = scoring_cfg.get("budget_unknown_score", 3)
    bud_score = _score_budget(budget, budget_unknown)

    # ── Weighted total ──
    total = (
        harington_match * weights.get("harington_match", 0.50)
        + legitimacy_score * weights.get("ao_legitimacy", 0.30)
        + dl_score * weights.get("deadline", 0.10)
        + bud_score * weights.get("budget", 0.10)
    )
    final_score = round(total * 2) / 2

    # Match percentage (Harington composite)
    match_pct = min(100, int((harington_match / 5) * 100))

    # Tier classification
    threshold = scoring_cfg.get("pertinence_threshold", 0.80)
    if match_pct >= threshold * 100:
        tier = "high"
    elif match_pct >= (threshold * 100) * 0.6:
        tier = "medium"
    else:
        tier = "low"

    # Match detail: prioritize products > REX > tech
    detail_parts = []
    for p in matched_products[:2]:
        detail_parts.append(p["label"])
    for r in matched_rex[:2]:
        detail_parts.append(f"REX {r['label']}")
    for t in matched_tech[:3]:
        detail_parts.append(t.title())
    match_detail = " · ".join(detail_parts[:5]) if detail_parts else "Pertinence faible"

    # Relevance summary (7-10 bullets)
    relevance_summary = _generate_relevance_summary(
        market, matched_products, matched_rex, matched_tech, ao_type, ao_legitimacy_raw
    )

    # Geography (display only)
    departments = market.get("code_departement", []) or []
    geo = _geo_label(departments, idf_depts)

    return {
        "score": final_score,
        "category": best_cat,
        "categories": matching_cats or [best_cat],
        "category_color": cat_color,
        "budget": budget,
        "days_left": days_left,
        "match_pct": match_pct,
        "match_detail": match_detail,
        "tier": tier,
        "ao_type": ao_type,
        "ao_legitimacy": ao_legitimacy_raw,
        "matched_products": [{"label": p["label"], "description": p["description"]} for p in matched_products],
        "matched_rex": [{"label": r["label"], "sector": r["sector"], "duration": r["duration"]} for r in matched_rex],
        "matched_tech": matched_tech[:10],
        "relevance_summary": relevance_summary,
        "geo_label": geo,
        "breakdown": {
            "harington_match": round(harington_match, 2),
            "harington_sub": {
                "tech_stack": tech_score,
                "product_match": product_score,
                "rex_sector": rex_score,
            },
            "ao_legitimacy": legitimacy_score,
            "deadline": dl_score,
            "budget": bud_score,
        },
    }


def score_all_markets(markets: list[dict], config: dict) -> tuple[list[dict], int]:
    """Score all markets, filter non-ESN, return (sorted_scored, filtered_count)."""
    scored = []
    filtered_count = 0
    for m in markets:
        result = score_market(m, config)
        if result is None:
            filtered_count += 1
            continue
        scored.append({**m, **result})

    scored.sort(key=lambda x: (-x["score"], x.get("days_left", 999)))
    return scored, filtered_count
