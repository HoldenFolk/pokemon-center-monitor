"""
Bloomreach Discovery API client for Pokemon Center product catalog.

Works from any server/GitHub Actions with no cookies, no browser, no bot detection.
Limitation: returns product catalog only — no stock availability data.
Use this to detect NEW products appearing (new set drops).
"""
import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BLOOMREACH_URL = "https://core.dxpapi.com/api/v1/core/"
ACCOUNT_ID = "6284"
DOMAIN_KEY = "pokemon"

HEADERS = {
    "accept": "application/json",
    "origin": "https://www.pokemoncenter.com",
    "referer": "https://www.pokemoncenter.com/",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}


def fetch_tcg_catalog(rows: int = 100, start: int = 0, sort: str = "") -> Optional[dict]:
    """
    Fetch a page of TCG products from the Bloomreach Discovery API.
    No bot detection — works from any IP.

    sort: optional Bloomreach sort string e.g. "launch_date+desc"
    Returns the raw API response dict, or None on failure.
    """
    params = {
        "account_id": ACCOUNT_ID,
        "domain_key": DOMAIN_KEY,
        "request_type": "search",
        "search_type": "keyword",
        "q": "*",
        "rows": str(rows),
        "start": str(start),
        "fl": "pid,title,price,sale_price,thumb_image,url,brand,launch_date",
        "fq": "cat_id:tcg-cards",
        "url": "https://www.pokemoncenter.com/category/tcg-cards",
        "_br_uid_2": "uid=monitor:v=13.0:ts=1721000000000:hc=1",
    }
    if sort:
        params["sort"] = sort
    try:
        r = requests.get(BLOOMREACH_URL, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error("Bloomreach API error: %s", e)
        return None


def get_all_tcg_products(max_pages: int = 5, sort: str = "") -> list[dict]:
    """
    Fetch all TCG products from Bloomreach, paginating up to max_pages.
    sort: optional Bloomreach sort string; "launch_date+desc" gets newest products first.
    Returns a flat list of product dicts with keys: pid, title, price, thumb_image, url, launch_date.
    """
    all_docs = []
    rows = 100

    for page in range(max_pages):
        start = page * rows
        data = fetch_tcg_catalog(rows=rows, start=start, sort=sort)
        if not data:
            break

        response = data.get("response", {})
        docs = response.get("docs", [])
        total = response.get("numFound", 0)

        all_docs.extend(docs)
        logger.info("Bloomreach page %d: %d/%d products fetched", page + 1, len(all_docs), total)

        if len(all_docs) >= total or not docs:
            break

        if page < max_pages - 1:
            time.sleep(0.5)

    return all_docs
