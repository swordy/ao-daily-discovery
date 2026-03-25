"""Scoring engine for BOAMP markets — Harington-specific, portfolio-driven.

NEW MODEL (v2):
  SCORE = Stack Match (50%) + Deadline (20%) + Budget (20%) + Geo (10%)

Stack Match is a composite of:
  - tech_stack (30%)
  - products (15%)
  - expertises (20%)
  - delivery (20%)
  - profils (15%)
"""

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


def _extract_description_text(market: dict) -> str:
    """Extract description text from market data (without BOAMP descriptors)."""
    parts = []
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
    """Find the best matching category for a market."""
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


# ── TERM MATCHING ──

def _term_in_text(term: str, text: str) -> bool:
    """Check if a term appears in text as a whole word (not substring).
    Uses word boundary matching for short terms to avoid false positives
    like 'ssis' matching inside 'assistance'.
    """
    if len(term) <= 5:
        return bool(re.search(r'(?<![a-zà-ÿ])' + re.escape(term) + r'(?![a-zà-ÿ])', text))
    return term in text


# ── STACK MATCH SUB-SCORES ──

def _score_tech_stack(text: str, tech_stack: dict) -> tuple[float, list[str]]:
    """Score tech stack match. Returns (score_1_5, matched_terms)."""
    all_techs = []
    for category_terms in tech_stack.values():
        all_techs.extend(category_terms)

    matched = []
    seen = set()
    for t in all_techs:
        tl = t.lower()
        if _term_in_text(tl, text) and tl not in seen:
            matched.append(t)
            seen.add(tl)

    count = len(matched)
    if count >= 5:
        score = 5
    elif count >= 3:
        score = 4
    elif count >= 2:
        score = 3.5
    elif count >= 1:
        score = 3
    else:
        score = 1

    return score, matched


def _score_products(text: str, products: dict) -> tuple[float, list[dict]]:
    """Score Harington product match. Returns (score_1_5, matched_products_info)."""
    matched_products = []

    for product_name, product_data in products.items():
        product_keywords = product_data.get("keywords", [])
        hits = sum(1 for kw in product_keywords if kw.lower() in text)
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

    strong_matches = [p for p in matched_products if p["hits"] >= 2]
    if len(strong_matches) >= 3:
        score = 5
    elif len(strong_matches) >= 2:
        score = 4
    elif len(strong_matches) >= 1:
        score = 3
    elif len(matched_products) >= 2:
        score = 2.5
    elif len(matched_products) >= 1:
        score = 2
    else:
        score = 1

    return score, matched_products


def _score_expertises(text: str, expertises: dict) -> tuple[float, list[str]]:
    """Score expertise domains match. Returns (score_1_5, matched_expertise_names).

    A domain matches with 2+ keyword hits, OR 1 hit if the keyword is a
    specific compound term (>= 12 chars) — handles short BOAMP descriptions.
    """
    matched_domains = []

    for domain_name, domain_keywords in expertises.items():
        matched_kws = [kw for kw in domain_keywords if kw.lower() in text]
        hits = len(matched_kws)
        if hits >= 2:
            matched_domains.append(domain_name)
        elif hits == 1 and len(matched_kws[0]) >= 12:
            # Single hit on a specific compound term (e.g. "plan d'actions ia")
            matched_domains.append(domain_name)

    count = len(matched_domains)
    if count >= 4:
        score = 5
    elif count >= 3:
        score = 4
    elif count >= 2:
        score = 3.5
    elif count >= 1:
        score = 3
    else:
        # Check for single keyword hits across all domains
        any_hit = False
        for domain_keywords in expertises.values():
            if any(kw.lower() in text for kw in domain_keywords):
                any_hit = True
                break
        score = 1.5 if any_hit else 1

    return score, matched_domains


def _score_delivery(text: str, delivery: dict) -> tuple[float, list[str]]:
    """Score delivery model match. Returns (score_1_5, matched_delivery_types)."""
    matched_types = []

    for delivery_name, delivery_keywords in delivery.items():
        hits = sum(1 for kw in delivery_keywords if _term_in_text(kw.lower(), text))
        if hits >= 1:
            matched_types.append(delivery_name)

    count = len(matched_types)
    if count >= 4:
        score = 5
    elif count >= 3:
        score = 4.5
    elif count >= 2:
        score = 4
    elif count >= 1:
        score = 3
    else:
        score = 1

    return score, matched_types


def _score_profils(text: str, profils: dict) -> tuple[float, list[str]]:
    """Score profile types match. Returns (score_1_5, matched_profile_types)."""
    matched_types = []

    for profil_name, profil_keywords in profils.items():
        hits = sum(1 for kw in profil_keywords if _term_in_text(kw.lower(), text))
        if hits >= 1:
            matched_types.append(profil_name)

    count = len(matched_types)
    if count >= 4:
        score = 5
    elif count >= 3:
        score = 4
    elif count >= 2:
        score = 3.5
    elif count >= 1:
        score = 3
    else:
        score = 1

    return score, matched_types


def _score_rex_sector(text: str, rex_list: list[dict], sectors: list[str]) -> tuple[float, list[dict]]:
    """Score REX and sector match. Returns (score_1_5, matched_rex_info).
    NOTE: This is for display/summary only, NOT part of the scored total.
    """
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
    return float(len(matched_rex)), matched_rex


# ── DEADLINE (20%) ──

def _score_deadline(deadline_str: str) -> tuple[float, int]:
    """Score deadline. <30 days = higher score. Returns (score_1_5, days_left)."""
    try:
        if "T" in deadline_str:
            deadline = datetime.fromisoformat(deadline_str.replace("Z", "+00:00")).date()
        else:
            deadline = date.fromisoformat(deadline_str)
        days_left = (deadline - date.today()).days

        if days_left < 0:
            score = 1  # Expired
        elif days_left <= 7:
            score = 5
        elif days_left <= 14:
            score = 4.5
        elif days_left <= 21:
            score = 4
        elif days_left <= 30:
            score = 3.5
        elif days_left <= 45:
            score = 2
        else:
            score = 1
        return float(score), days_left
    except (ValueError, TypeError):
        return 1.0, 999


# ── BUDGET (20%) ──

def _score_budget(budget: float | None, unknown_score: int = 2) -> float:
    """Score budget. >50k = higher score."""
    if budget is not None:
        if budget >= 500000:
            return 5
        if budget >= 200000:
            return 4.5
        if budget >= 100000:
            return 4
        if budget >= 50000:
            return 3.5
        if budget >= 20000:
            return 2
        return 1
    return float(unknown_score)


# ── GEO (10%) ──

IDF_DEPARTMENTS = {"75", "77", "78", "91", "92", "93", "94", "95"}


def _score_geo(departments: list, idf_depts: set | None = None) -> tuple[float, str]:
    """Score geographic zone. IDF or National = best. Returns (score_1_5, label)."""
    if idf_depts is None:
        idf_depts = IDF_DEPARTMENTS

    if not departments:
        # National / no restriction = good for ESN
        return 4.0, "National"

    dept_set = set(departments)

    if dept_set & idf_depts:
        return 5.0, "Île-de-France"

    if len(departments) > 3:
        # Multi-department = quasi-national
        return 3.5, "National"

    # Regional: less interesting for Harington
    label = f"Dept. {', '.join(departments)}"
    return 2.0, label


# ── AO TYPE DETECTION (display/summary, enriches match detail) ──

def _detect_ao_type(text: str, service_types: dict) -> tuple[str, int]:
    """Detect AO service type for display. Returns (best_type_label, legitimacy_raw)."""
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

    return best_type, best_legitimacy


# ── RELEVANCE SUMMARY ──

_DELIVERY_LABELS = {
    "centre_competences": "Centre de compétences",
    "centre_services": "Centre de services",
    "nearshore_onshore": "Nearshore/Onshore",
    "forfait": "Forfait",
    "regie": "Régie / AT",
    "tma": "TMA / Maintenance applicative",
}

_PROFIL_LABELS = {
    "business_analyse": "Business Analyste",
    "gestion_projet": "Chef de projet / PMO",
    "moa": "MOA / AMOA",
    "product_owner": "Product Owner / Scrum Master",
    "architecte": "Architecte / Tech Lead",
    "consultant_it": "Consultant IT",
}

_EXPERTISE_LABELS = {
    "audit_urbanisation": "Audit & Urbanisation SI",
    "migration_bi_data": "Migration BI/Data/ETL",
    "microservices_archi": "Architecture Microservices",
    "iam_securite": "IAM & Sécurité",
    "rse_impact": "RSE & Impact",
    "qa_tests": "QA & Tests",
    "paiements_monetique": "Moyens de paiement",
    "ia_generative": "IA Générative & Agentique",
    "ecommerce_digital": "E-commerce & Digital",
    "modernisation_legacy": "Modernisation & Legacy",
}


def _generate_relevance_summary(
    market: dict,
    matched_products: list[dict],
    matched_rex: list[dict],
    matched_tech: list[str],
    matched_expertises: list[str],
    matched_delivery: list[str],
    matched_profils: list[str],
    ao_type: str,
    ao_legitimacy: int,
    geo_label: str,
) -> list[str]:
    """Generate 7-10 bullet points relevance summary (deterministic, no LLM)."""
    bullets = []

    objet = market.get("objet", "")
    buyer = market.get("nomacheteur", "")

    # 1-2 bullets: AO subject
    bullets.append(f"Objet : {objet[:150]}")
    if buyer:
        bullets.append(f"Acheteur : {buyer}")

    # 1 bullet: AO type
    if ao_legitimacy >= 4:
        bullets.append(f"Type d'AO \"{ao_type}\" : forte légitimité Harington")
    elif ao_legitimacy >= 3:
        bullets.append(f"Type d'AO \"{ao_type}\" : légitimité correcte")
    else:
        bullets.append(f"Type d'AO \"{ao_type}\" : légitimité limitée")

    # Delivery model
    if matched_delivery:
        labels = [_DELIVERY_LABELS.get(d, d) for d in matched_delivery]
        bullets.append(f"Mode de delivery : {', '.join(labels)}")

    # Tech stack
    if matched_tech:
        tech_display = ", ".join(t.title() for t in matched_tech[:8])
        bullets.append(f"Stack technique : {tech_display}")

    # Expertises
    if matched_expertises:
        labels = [_EXPERTISE_LABELS.get(e, e) for e in matched_expertises]
        bullets.append(f"Expertises matchées : {', '.join(labels)}")

    # Profils
    if matched_profils:
        labels = [_PROFIL_LABELS.get(p, p) for p in matched_profils]
        bullets.append(f"Profils demandés : {', '.join(labels)}")

    # Products
    if matched_products:
        for prod in matched_products[:2]:
            bullets.append(f"Produit applicable : {prod['label']} — {prod['description']}")

    # REX
    if matched_rex:
        for rex in matched_rex[:2]:
            bullets.append(f"REX sectoriel : {rex['label']} ({rex['sector']}, {rex['duration']})")

    # Pad to 7 minimum
    if len(bullets) < 7:
        bullets.append(f"Zone géographique : {geo_label}")
    if len(bullets) < 7:
        deadline = market.get("datelimitereponse", "")
        if deadline:
            bullets.append(f"Date limite de réponse : {deadline[:10]}")
    if len(bullets) < 7:
        descripteurs = market.get("descripteur_libelle", [])
        if descripteurs:
            bullets.append(f"Descripteurs BOAMP : {', '.join(descripteurs[:4])}")

    return bullets[:10]


# ── DEEP ANALYSIS (90%+ markets) ──

_DIM_LABELS = {
    "tech_stack": "Stack Technique",
    "products": "Accélérateurs & Offres",
    "expertises": "Expertises",
    "delivery": "Modes de Delivery",
    "profils": "Profils Consultants",
}

_DIM_WEIGHT_LABELS = {
    "tech_stack": 30,
    "products": 15,
    "expertises": 20,
    "delivery": 20,
    "profils": 15,
}

_SCORE_LABELS = {
    5: "Excellent",
    4.5: "Très fort",
    4: "Fort",
    3.5: "Bon",
    3: "Correct",
    2.5: "Partiel",
    2: "Faible",
    1.5: "Marginal",
    1: "Non couvert",
}


def _score_label(score: float) -> str:
    """Human-readable label for a score value."""
    rounded = round(score * 2) / 2
    return _SCORE_LABELS.get(rounded, _SCORE_LABELS.get(int(rounded), "—"))


def _build_deep_analysis(
    market: dict,
    stack_match: float,
    breakdown_sub: dict,
    dl_score: float,
    bud_score: float,
    geo_score: float,
    match_pct: int,
    ao_type: str,
    ao_legitimacy: int,
    matched_tech: list[str],
    matched_products: list[dict],
    matched_expertises: list[str],
    matched_delivery: list[str],
    matched_profils: list[str],
    matched_rex: list[dict],
    geo_label: str,
    budget: float | None,
    days_left: int,
) -> dict:
    """Build structured deep analysis for high-confidence markets (90%+).

    Follows scientific evaluation norms:
    - Multi-criteria weighted scoring (AHP-inspired)
    - Dimension-level granularity with item traceability
    - Synergy detection (cross-dimension reinforcement)
    - Gap analysis (missing dimensions)
    - Confidence reasoning (why this score)
    """
    # ── 1. Dimension breakdown ──
    dimensions = []
    for dim_key in ["tech_stack", "products", "expertises", "delivery", "profils"]:
        score = breakdown_sub.get(dim_key, 1)
        weight = _DIM_WEIGHT_LABELS.get(dim_key, 0)
        label = _DIM_LABELS.get(dim_key, dim_key)

        # Collect matched items per dimension
        if dim_key == "tech_stack":
            items = [t.title() for t in matched_tech[:8]]
        elif dim_key == "products":
            items = [f"{p['label']} — {p['description']}" for p in matched_products[:3]]
        elif dim_key == "expertises":
            items = list(matched_expertises[:4])
        elif dim_key == "delivery":
            items = list(matched_delivery[:4])
        elif dim_key == "profils":
            items = list(matched_profils[:4])
        else:
            items = []

        dimensions.append({
            "key": dim_key,
            "label": label,
            "score": round(score, 1),
            "score_label": _score_label(score),
            "weight_pct": weight,
            "contribution": round(score * weight / 100, 2),
            "items": items,
            "status": "strong" if score >= 4 else ("good" if score >= 3 else ("weak" if score >= 2 else "gap")),
        })

    # ── 2. Global dimensions (deadline, budget, geo) ──
    context_dims = [
        {
            "label": "Échéance",
            "score": round(dl_score, 1),
            "score_label": _score_label(dl_score),
            "detail": f"J-{days_left}" if days_left < 999 else "Non communiquée",
            "weight_pct": 20,
        },
        {
            "label": "Budget",
            "score": round(bud_score, 1),
            "score_label": _score_label(bud_score),
            "detail": f"{int(budget / 1000)}k€" if budget and budget >= 1000 else ("Non communiqué" if not budget else f"{int(budget)}€"),
            "weight_pct": 20,
        },
        {
            "label": "Géographie",
            "score": round(geo_score, 1),
            "score_label": _score_label(geo_score),
            "detail": geo_label,
            "weight_pct": 10,
        },
    ]

    # ── 3. Strengths (score >= 4) ──
    strengths = []
    for d in dimensions:
        if d["score"] >= 4 and d["items"]:
            strengths.append({
                "dimension": d["label"],
                "score": d["score"],
                "detail": ", ".join(d["items"][:3]),
            })

    # ── 4. Gaps / weaknesses (score < 3) ──
    _GAP_RECOMMENDATIONS = {
        "tech_stack": "Stack non explicitement mentionnée dans l'AO — vérifier le CCTP pour identifier les technos requises",
        "products": "Aucun accélérateur Harington directement applicable — positionner les outils internes comme avantage concurrentiel dans le mémoire technique",
        "expertises": "Domaine d'expertise non détecté — identifier l'angle métier dans le CCTP et mapper vers les REX Harington",
        "delivery": "Mode de delivery non précisé dans l'AO — proposer le modèle le plus adapté (CDS/forfait/régie) dans la réponse",
        "profils": "Profils requis non explicitement mentionnés — vérifier les compétences demandées dans le RC/CCTP",
    }
    gaps = []
    for d in dimensions:
        if d["score"] < 3:
            gaps.append({
                "dimension": d["label"],
                "score": d["score"],
                "recommendation": _GAP_RECOMMENDATIONS.get(d["key"], f"Dimension \"{d['label']}\" à approfondir dans le DCE"),
            })

    # ── 5. Synergy flags ──
    synergies = []
    tech_s = breakdown_sub.get("tech_stack", 1)
    delivery_s = breakdown_sub.get("delivery", 1)
    expertise_s = breakdown_sub.get("expertises", 1)
    profil_s = breakdown_sub.get("profils", 1)
    product_s = breakdown_sub.get("products", 1)

    if delivery_s >= 4 and tech_s >= 3:
        synergies.append("Delivery + Stack : cœur de métier ESN couvert (CDS/TMA + techno maîtrisée)")
    if profil_s >= 3 and expertise_s >= 3:
        synergies.append("Profils + Expertises : consulting ESN pur (consultants qualifiés + domaine d'expertise)")
    if product_s >= 3 and tech_s >= 3:
        synergies.append("Produit Harington + Stack : offre packagée applicable (time-to-value accéléré)")
    if delivery_s >= 3 and profil_s >= 3:
        synergies.append("Delivery + Profils : capacité de staffing confirmée (mise à disposition de ressources)")
    if expertise_s >= 3 and delivery_s >= 3 and tech_s >= 3:
        synergies.append("Triple convergence : expertise + delivery + stack → fit stratégique majeur")

    # ── 6. AO type assessment ──
    if ao_legitimacy >= 4:
        ao_assessment = f"Type d'AO \"{ao_type}\" — forte légitimité Harington, historique de succès sur ce segment"
    elif ao_legitimacy >= 3:
        ao_assessment = f"Type d'AO \"{ao_type}\" — légitimité correcte, positionnement crédible"
    else:
        ao_assessment = f"Type d'AO \"{ao_type}\" — légitimité limitée, nécessite un angle différenciant"

    # ── 7. Fit summary ──
    strong_count = sum(1 for d in dimensions if d["status"] == "strong")
    good_count = sum(1 for d in dimensions if d["status"] == "good")

    if strong_count >= 3:
        fit_summary = "Adéquation excellente — alignement fort sur 3+ dimensions clés. Dossier prioritaire."
    elif strong_count >= 2:
        fit_summary = "Très bonne adéquation — 2 dimensions fortes. Positionnement solide."
    elif strong_count >= 1 and good_count >= 2:
        fit_summary = "Bonne adéquation — 1 dimension forte + couverture correcte. À creuser."
    else:
        fit_summary = "Adéquation partielle — score élevé grâce aux synergies. Valider le positionnement."

    # ── 8. Confidence reasoning ──
    reasons = []
    if strong_count >= 2:
        reasons.append(f"{strong_count} sous-dimensions ≥ 4/5")
    if synergies:
        reasons.append(f"{len(synergies)} synergies détectées")
    if ao_legitimacy >= 4:
        reasons.append("type d'AO à forte légitimité")
    if matched_products:
        reasons.append(f"{len(matched_products)} produit(s) Harington applicable(s)")
    if matched_rex:
        reasons.append(f"{len(matched_rex)} REX sectoriel(s) pertinent(s)")

    # ── 9. Strategic recommendation ──
    if match_pct >= 90 and days_left <= 14:
        recommendation = "GO IMMÉDIAT — Forte adéquation + échéance proche. Mobiliser l'équipe AO."
    elif match_pct >= 90:
        recommendation = "GO — Forte adéquation. Préparer le dossier en anticipation."
    elif match_pct >= 70 and days_left <= 14:
        recommendation = "GO/NO-GO à arbitrer — Adéquation correcte mais échéance serrée."
    else:
        recommendation = "À QUALIFIER — Analyser les exigences détaillées avant engagement."

    return {
        "fit_summary": fit_summary,
        "ao_assessment": ao_assessment,
        "recommendation": recommendation,
        "stack_match_score": round(stack_match, 2),
        "dimensions": dimensions,
        "context_dims": context_dims,
        "strengths": strengths,
        "gaps": gaps,
        "synergies": synergies,
        "confidence": ", ".join(reasons) if reasons else "Score composite élevé",
        "rex": [{"label": r["label"], "sector": r["sector"], "duration": r["duration"]} for r in matched_rex[:3]],
        "products_applicable": [{"label": p["label"], "description": p["description"]} for p in matched_products[:3]],
    }


# ── MAIN SCORING FUNCTION ──

def score_market(market: dict, config: dict) -> dict | None:
    """Score a market with Harington-specific matching.

    NEW MODEL:
      SCORE = Stack Match (50%) + Deadline (20%) + Budget (20%) + Geo (10%)

    Stack Match = tech_stack(30%) + products(15%) + expertises(20%) + delivery(20%) + profils(15%)

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
    sub_weights = scoring_cfg.get("stack_match_sub_weights", {})
    categories = config.get("categories", {})
    idf_depts = set(config.get("departments_idf", list(IDF_DEPARTMENTS)))

    # Category for display/filter
    best_cat, _, matching_cats = _best_category(text, categories)
    cat_color = categories.get(best_cat, {}).get("color", "#6B7280")
    source_cats = market.get("_source_categories", [])
    for sc in source_cats:
        if sc not in matching_cats:
            matching_cats.append(sc)

    # ── SOFT EXCLUSION: hardware/infra/ERP propriétaire/réseau ──
    # Match on objet + description only (NOT BOAMP descriptors which tag everything as "maintenance serveurs")
    soft_excl = config.get("soft_exclusions", {})
    soft_cap = None
    if soft_excl.get("enabled", False):
        objet_text = market.get("objet", "").lower()
        desc_text = _extract_description_text(market)
        check_text = f"{objet_text} {desc_text}"

        max_pct = soft_excl.get("max_match_pct", 30)
        hit_objet = [kw for kw in soft_excl.get("keywords_objet", []) if kw.lower() in check_text]
        hit_erp = [kw for kw in soft_excl.get("keywords_erp_proprietary", []) if _term_in_text(kw.lower(), check_text)]
        hit_vendor = [kw for kw in soft_excl.get("vendor_infra", []) if _term_in_text(kw.lower(), check_text)]

        if hit_objet or hit_erp or hit_vendor:
            soft_cap = (max_pct / 100) * 5  # e.g. 30% → 1.5/5

    # ── 1. STACK MATCH (50%) — composite of 5 sub-dimensions ──
    tech_score, matched_tech = _score_tech_stack(text, portfolio.get("tech_stack", {}))
    product_score, matched_products = _score_products(text, portfolio.get("products", {}))
    expertise_score, matched_expertises = _score_expertises(text, portfolio.get("expertises", {}))
    delivery_score, matched_delivery = _score_delivery(text, portfolio.get("delivery", {}))
    profil_score, matched_profils = _score_profils(text, portfolio.get("profils", {}))

    # ── IMPLICIT IT STAFFING ──
    # BOAMP texts often just say "informatique + prestations de services" without
    # naming profiles or delivery modes. This IS IT staffing — Harington can source.
    is_it_services = ("informatique" in text and "prestations" in text) or "services numériques" in text

    stack_match = (
        tech_score * sub_weights.get("tech_stack", 0.30)
        + product_score * sub_weights.get("products", 0.15)
        + expertise_score * sub_weights.get("expertises", 0.20)
        + delivery_score * sub_weights.get("delivery", 0.20)
        + profil_score * sub_weights.get("profils", 0.15)
    )

    # ── AT/RÉGIE FAST-TRACK ──
    # Explicit AT/régie delivery + profile match → Harington can source any
    # consultant regardless of domain → floor at 4.5/5.
    _AT_DELIVERY_KEYS = {"regie", "centre_competences", "nearshore_onshore"}
    is_explicit_staffing = bool(_AT_DELIVERY_KEYS & set(matched_delivery))

    if is_explicit_staffing and profil_score >= 3:
        stack_match = max(4.5, stack_match)

    # ── IT SERVICES STAFFING BOOST ──
    # "informatique + prestations de services" = IT staffing request.
    # Harington can source any IT profile → floor based on natural matches.
    elif is_it_services:
        # Count naturally matching dimensions (>= 3 score, before any boost)
        natural_strong = sum(1 for s in [tech_score, expertise_score, delivery_score, profil_score] if s >= 3)
        if natural_strong >= 2:
            stack_match = max(4.5, stack_match)  # 2+ natural matches + IT services
        elif natural_strong >= 1:
            stack_match = max(3.5, stack_match)  # 1 natural match + IT services
        else:
            stack_match = max(3.0, stack_match)  # IT services but nothing specific

    # ── ESN BASELINE BOOST ──
    # Non-IT-services markets with strong dimensions
    elif delivery_score >= 3 and profil_score >= 3:
        strong_dims = sum(1 for s in [tech_score, expertise_score, delivery_score, profil_score] if s >= 3)
        if strong_dims >= 3:
            stack_match = max(4.0, stack_match)
        elif strong_dims >= 2:
            stack_match = max(3.5, stack_match)

    # ── SYNERGY BONUS ──
    # Core ESN combos that must score high
    if delivery_score >= 4 and tech_score >= 3:
        stack_match += 0.8  # CDS/TMA + stack core = coeur de métier ESN
    elif delivery_score >= 3 and tech_score >= 4:
        stack_match += 0.6  # Delivery + stack riche
    elif profil_score >= 4 and expertise_score >= 3:
        stack_match += 1.5  # Consulting ESN pur: profils (BA, PMO, MOA) + expertises (audit, urba)
    elif profil_score >= 3 and expertise_score >= 3:
        stack_match += 1.0  # Profils + expertises reconnus
    elif delivery_score >= 4 and expertise_score >= 3:
        stack_match += 0.5  # Delivery forte + expertise reconnue
    elif delivery_score >= 3 and profil_score >= 3:
        stack_match += 0.5  # Delivery + profils ESN

    # IA strategic bonus: IA tech + delivery + expertise IA
    if expertise_score >= 3 and delivery_score >= 3 and tech_score >= 3:
        stack_match += 0.7  # Triple combo: expertise + delivery + tech

    stack_match = min(5.0, stack_match)

    # ── SOFT EXCLUSION CAP ──
    # Hardware/infra/réseau/ERP propriétaire → cap stack_match at 30%
    if soft_cap is not None:
        stack_match = min(soft_cap, stack_match)

    # REX: computed for display/relevance summary only, NOT scored
    _, matched_rex = _score_rex_sector(
        text, portfolio.get("rex", []), portfolio.get("sectors", [])
    )

    # AO type: for display & summary enrichment
    ao_type, ao_legitimacy_raw = _detect_ao_type(text, portfolio.get("service_types", {}))

    # ── 2. DEADLINE (20%) ──
    deadline_str = market.get("datelimitereponse", "")
    dl_score, days_left = _score_deadline(deadline_str)

    # ── 3. BUDGET (20%) ──
    budget = _extract_budget(market)
    budget_unknown = scoring_cfg.get("budget_unknown_score", 2)
    bud_score = _score_budget(budget, budget_unknown)

    # ── 4. GEO (10%) ──
    departments = market.get("code_departement", []) or []
    geo_score, geo_label = _score_geo(departments, idf_depts)

    # ── Weighted total ──
    total = (
        stack_match * weights.get("stack_match", 0.50)
        + dl_score * weights.get("deadline", 0.20)
        + bud_score * weights.get("budget", 0.20)
        + geo_score * weights.get("geo", 0.10)
    )
    final_score = round(total * 2) / 2

    # Match percentage (Stack composite)
    match_pct = min(100, int((stack_match / 5) * 100))

    # Tier classification
    threshold = scoring_cfg.get("pertinence_threshold", 0.70)
    if match_pct >= threshold * 100:
        tier = "high"
    elif match_pct >= (threshold * 100) * 0.6:
        tier = "medium"
    else:
        tier = "low"

    # Match detail: prioritize delivery > expertises > tech > products > profils
    detail_parts = []
    for d in matched_delivery[:2]:
        detail_parts.append(_DELIVERY_LABELS.get(d, d))
    for e in matched_expertises[:2]:
        detail_parts.append(_EXPERTISE_LABELS.get(e, e))
    for t in matched_tech[:3]:
        detail_parts.append(t.title())
    for p in matched_products[:1]:
        detail_parts.append(p["label"])
    for pr in matched_profils[:1]:
        detail_parts.append(_PROFIL_LABELS.get(pr, pr))
    for r in matched_rex[:1]:
        detail_parts.append(f"REX {r['label']}")
    match_detail = " · ".join(detail_parts[:6]) if detail_parts else "Pertinence faible"

    # Relevance summary (7-10 bullets)
    relevance_summary = _generate_relevance_summary(
        market, matched_products, matched_rex, matched_tech,
        matched_expertises, matched_delivery, matched_profils,
        ao_type, ao_legitimacy_raw, geo_label,
    )

    # Deep analysis for 90%+ markets
    deep_analysis = None
    if match_pct >= 90:
        deep_analysis = _build_deep_analysis(
            market=market,
            stack_match=stack_match,
            breakdown_sub={
                "tech_stack": tech_score,
                "products": product_score,
                "expertises": expertise_score,
                "delivery": delivery_score,
                "profils": profil_score,
            },
            dl_score=dl_score,
            bud_score=bud_score,
            geo_score=geo_score,
            match_pct=match_pct,
            ao_type=ao_type,
            ao_legitimacy=ao_legitimacy_raw,
            matched_tech=matched_tech,
            matched_products=matched_products,
            matched_expertises=[_EXPERTISE_LABELS.get(e, e) for e in matched_expertises],
            matched_delivery=[_DELIVERY_LABELS.get(d, d) for d in matched_delivery],
            matched_profils=[_PROFIL_LABELS.get(p, p) for p in matched_profils],
            matched_rex=matched_rex,
            geo_label=geo_label,
            budget=budget,
            days_left=days_left,
        )

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
        "matched_expertises": [_EXPERTISE_LABELS.get(e, e) for e in matched_expertises],
        "matched_delivery": [_DELIVERY_LABELS.get(d, d) for d in matched_delivery],
        "matched_profils": [_PROFIL_LABELS.get(p, p) for p in matched_profils],
        "relevance_summary": relevance_summary,
        "geo_label": geo_label,
        "deep_analysis": deep_analysis,
        "breakdown": {
            "stack_match": round(stack_match, 2),
            "stack_sub": {
                "tech_stack": tech_score,
                "products": product_score,
                "expertises": expertise_score,
                "delivery": delivery_score,
                "profils": profil_score,
            },
            "deadline": dl_score,
            "budget": bud_score,
            "geo": geo_score,
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
