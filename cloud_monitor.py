#!/usr/bin/env python3
"""
Cloud-based Pokemon Center TCG new-product monitor.

Uses the Bloomreach Discovery API — no browser, no cookies, no DataDome.
Works from GitHub Actions (ubuntu-latest), any VPS, or any server.

Limitation: detects NEW products appearing in the catalog only.
Cannot detect restocks (Bloomreach has no availability data).
Restock detection requires the browser-based monitor.py on a Mac or self-hosted runner.

Quick start:
  python cloud_monitor.py --dry-run   # Test without sending notifications
  python cloud_monitor.py             # Run one check cycle
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

import yaml

from bloomreach import get_all_tcg_products
from notifier import NtfyNotifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

STATE_FILE = os.environ.get("BLOOMREACH_STATE_FILE", "bloomreach_state.json")


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"seen_products": {}, "last_run": None}


def save_state(s: dict) -> None:
    s["last_run"] = datetime.now(timezone.utc).isoformat()
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    logger.info("State saved to %s (%d products)", STATE_FILE, len(s.get("seen_products", {})))


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def is_priority(name: str, keywords: list[str]) -> bool:
    lower = name.lower()
    return any(kw.lower() in lower for kw in keywords)


def is_excluded(name: str, keywords: list[str]) -> bool:
    lower = name.lower()
    return any(kw.lower() in lower for kw in keywords)


def build_product_url(product: dict) -> str:
    """Construct a Pokemon Center product URL from the Bloomreach product record."""
    # Bloomreach url field: '/product/code/slug' — prepend the domain
    raw_url = product.get("url", "")
    if raw_url.startswith("/"):
        return f"https://www.pokemoncenter.com{raw_url}"
    if raw_url.startswith("http"):
        return raw_url
    pid = product.get("pid", "")
    return f"https://www.pokemoncenter.com/product/{pid}"


def format_price(product: dict) -> str:
    sale = product.get("sale_price")
    regular = product.get("price")
    p = sale or regular
    if p:
        try:
            return f"${float(p):.2f}"
        except (ValueError, TypeError):
            return str(p)
    return ""


def run_once(config: dict, dry_run: bool = False) -> None:
    cfg = config.get("monitoring", {})
    ntfy_cfg = config.get("ntfy", {})
    priority_keywords = cfg.get("priority_keywords", [])
    exclude_keywords = cfg.get("exclude_keywords", [])

    ntfy_topic = os.environ.get("NTFY_TOPIC") or ntfy_cfg.get("topic", "")
    ntfy_server = os.environ.get("NTFY_SERVER") or ntfy_cfg.get("server", "https://ntfy.sh")
    notifier = NtfyNotifier(topic=ntfy_topic, server=ntfy_server)

    current_state = load_state()
    seen = current_state.setdefault("seen_products", {})
    is_first_run = len(seen) == 0

    if is_first_run:
        # Seed the full catalog on first run (80 pages × 100 = up to 8,000 products)
        # so we don't false-alarm on the next run for old products.
        logger.info("First run: fetching full TCG catalog to seed state...")
        products = get_all_tcg_products(max_pages=80)
    else:
        # Subsequent runs: sort newest-first and check the top 300.
        # New drops appear at the front of the list; fetching 3 pages is enough.
        logger.info("Checking for new TCG products (newest-first, top 300)...")
        products = get_all_tcg_products(max_pages=3, sort="launch_date+desc")

    logger.info("Bloomreach returned %d products", len(products))

    new_count = 0
    for product in products:
        pid = product.get("pid", "")
        name = product.get("title", "Unknown")

        if not pid:
            continue

        if is_excluded(name, exclude_keywords):
            continue

        url = build_product_url(product)
        price = format_price(product)

        if pid not in seen:
            if is_first_run:
                # Silent seed — don't spam on first run
                logger.debug("First run: seeding %s", name)
            else:
                new_count += 1
                priority = "high" if is_priority(name, priority_keywords) else "default"
                logger.info("NEW PRODUCT DETECTED: %s (%s)", name, pid)
                if not dry_run:
                    notifier.send_new_product(name, price, url)
                else:
                    logger.info("[DRY RUN] Would notify: new product %s", name)

            seen[pid] = {
                "name": name,
                "url": url,
                "price": price,
                "first_seen": datetime.now(timezone.utc).isoformat(),
            }

    if is_first_run:
        logger.info("First run complete — seeded %d products (no notifications sent)", len(seen))
    else:
        logger.info("Cycle complete — %d new products found", new_count)

    save_state(current_state)


def main():
    parser = argparse.ArgumentParser(
        description="Cloud-based TCG new-product monitor (Bloomreach API, no browser)"
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Log but do not send notifications")
    parser.add_argument("--test-notify", action="store_true", help="Send a test notification and exit")
    args = parser.parse_args()

    config = load_config(args.config)

    if args.test_notify:
        ntfy_cfg = config.get("ntfy", {})
        topic = os.environ.get("NTFY_TOPIC") or ntfy_cfg.get("topic", "")
        server = os.environ.get("NTFY_SERVER") or ntfy_cfg.get("server", "https://ntfy.sh")
        notifier = NtfyNotifier(topic=topic, server=server)
        success = notifier.send_test()
        sys.exit(0 if success else 1)

    run_once(config, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
