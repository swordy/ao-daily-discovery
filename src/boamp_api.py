"""Client API BOAMP — OpenDataSoft public endpoint."""

import requests
from concurrent.futures import ThreadPoolExecutor
from datetime import date

BASE_URL = "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records"

SEARCH_QUERIES = [
    ["intelligence artificielle", "services numériques"],
    ["IA", "agentique", "développement"],
    ["LLM", "chatbot", "plateforme IA"],
    ["innovation numérique", "data", "IA"],
    ["usine logicielle", "dev factory", "IA"],
    ["centre de service", "centre de compétences", "développement logiciel"],
    [".Net", "C#", "Java", "spring"],
    ["React", "Angular", "Python"],
]


def _build_where(keywords: list[str], today: str, departments: list[str] | None = None) -> str:
    """Build the WHERE clause for the API query."""
    kw_clauses = []
    for kw in keywords:
        escaped = kw.replace("'", "\\'")
        kw_clauses.append(
            f"(objet LIKE '%{escaped}%' OR descripteur_libelle LIKE '%{escaped}%')"
        )
    where = f"({' OR '.join(kw_clauses)})"
    where += f" AND datelimitereponse >= date'{today}'"
    if departments:
        dept_clauses = [f'code_departement="{d}"' for d in departments]
        where += f" AND ({' OR '.join(dept_clauses)})"
    return where


def search_markets(
    keywords: list[str],
    market_type: str = "SERVICES",
    limit: int = 20,
    sort_by: str = "datelimitereponse ASC",
    departments: list[str] | None = None,
) -> list[dict]:
    """Search BOAMP markets for given keywords."""
    today = date.today().isoformat()
    params = {
        "where": _build_where(keywords, today, departments),
        "limit": limit,
        "order_by": sort_by,
    }
    if market_type:
        params["refine"] = f"type_marche:{market_type}"

    try:
        resp = requests.get(BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", [])
    except requests.RequestException as e:
        print(f"[WARN] BOAMP API error for {keywords}: {e}")
        return []


def get_market_details(idweb: str) -> dict | None:
    """Get full details for a specific market."""
    params = {"where": f'idweb="{idweb}"'}
    try:
        resp = requests.get(BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return results[0] if results else None
    except requests.RequestException as e:
        print(f"[WARN] BOAMP API error for {idweb}: {e}")
        return None


def fetch_all_markets() -> list[dict]:
    """Run all 8 search queries in parallel, deduplicate by idweb."""
    all_markets = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(search_markets, q) for q in SEARCH_QUERIES]
        for f in futures:
            all_markets.extend(f.result())

    # Deduplicate by idweb
    seen = set()
    unique = []
    for m in all_markets:
        idweb = m.get("idweb")
        if idweb and idweb not in seen:
            seen.add(idweb)
            unique.append(m)

    return unique
