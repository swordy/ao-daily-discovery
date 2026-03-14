"""Client API BOAMP — OpenDataSoft public endpoint, config-driven."""

import json
import requests
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

BASE_URL = "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records"
CONFIG_PATH = Path(__file__).parent.parent / "config.json"


def load_config(path: str | Path | None = None) -> dict:
    """Load configuration from JSON file."""
    p = Path(path) if path else CONFIG_PATH
    with open(p, encoding="utf-8") as f:
        return json.load(f)


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


def fetch_all_markets(config: dict | None = None) -> list[dict]:
    """Run all search queries from config in parallel, deduplicate by idweb."""
    if config is None:
        config = load_config()

    categories = config.get("categories", {})
    api_cfg = config.get("api", {})
    market_type = api_cfg.get("market_type", "SERVICES")
    limit = api_cfg.get("limit_per_query", 20)
    max_workers = api_cfg.get("max_workers", 12)

    # Build list of (category_name, keywords) tasks
    tasks = []
    for cat_name, cat_data in categories.items():
        for query_keywords in cat_data.get("queries", []):
            tasks.append((cat_name, query_keywords))

    # Execute all queries in parallel
    results_by_task = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            (cat_name, executor.submit(search_markets, kws, market_type, limit))
            for cat_name, kws in tasks
        ]
        for cat_name, future in futures:
            for market in future.result():
                results_by_task.append((cat_name, market))

    # Deduplicate by idweb, tracking source categories
    seen: dict[str, dict] = {}
    for cat_name, market in results_by_task:
        idweb = market.get("idweb")
        if not idweb:
            continue
        if idweb not in seen:
            market["_source_categories"] = [cat_name]
            seen[idweb] = market
        else:
            cats = seen[idweb].get("_source_categories", [])
            if cat_name not in cats:
                cats.append(cat_name)

    return list(seen.values())
