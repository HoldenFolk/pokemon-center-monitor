import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

STATE_FILE = os.environ.get("STATE_FILE", "state.json")

DEFAULT_STATE = {
    "seen_products": {},
    "last_run": None,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load() -> dict:
    """Load state from disk. Returns default state if file doesn't exist."""
    if not os.path.exists(STATE_FILE):
        logger.info("No state file found at %s — starting fresh", STATE_FILE)
        return dict(DEFAULT_STATE, seen_products={})

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info("Loaded state with %d known products", len(data.get("seen_products", {})))
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load state file: %s — starting fresh", e)
        return dict(DEFAULT_STATE, seen_products={})


def save(state: dict) -> None:
    """Persist state to disk."""
    state["last_run"] = _now_iso()
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        logger.info("State saved (%d products tracked)", len(state.get("seen_products", {})))
    except OSError as e:
        logger.error("Failed to save state: %s", e)


def is_new_product(state: dict, product_id: str) -> bool:
    return product_id not in state.get("seen_products", {})


def was_sold_out(state: dict, product_id: str) -> bool:
    product = state.get("seen_products", {}).get(product_id)
    if product is None:
        return False
    return product.get("status") == "sold_out"


def get_product_status(state: dict, product_id: str) -> Optional[str]:
    product = state.get("seen_products", {}).get(product_id)
    return product.get("status") if product else None


def upsert_product(state: dict, product_id: str, name: str, url: str,
                   in_stock: Optional[bool], price: Optional[str] = None) -> None:
    """Insert or update a product record in state."""
    products = state.setdefault("seen_products", {})
    now = _now_iso()

    if in_stock is True:
        status = "in_stock"
    elif in_stock is False:
        status = "sold_out"
    else:
        status = "unknown"

    if product_id in products:
        products[product_id]["status"] = status
        products[product_id]["last_checked"] = now
        if price:
            products[product_id]["price"] = price
    else:
        products[product_id] = {
            "name": name,
            "url": url,
            "status": status,
            "price": price,
            "first_seen": now,
            "last_checked": now,
        }
