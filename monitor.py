#!/usr/bin/env python3
"""
Pokemon Center TCG Drop Monitor
Watches for new TCG products and restocks; sends ntfy.sh push notifications.

Quick start:
  python monitor.py --setup      # One-time: warm up browser session
  python monitor.py --dry-run   # Test without sending notifications
  python monitor.py              # Run one check cycle
  python monitor.py --loop       # Run continuously every N minutes
"""
import argparse
import logging
import os
import sys
import time

import yaml

import state
from fetcher import DEFAULT_DATA_DIR, Fetcher
from notifier import NtfyNotifier
from parser import Product, parse_category_page, parse_product_page

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Max sold-out priority products to check individually per run
MAX_RESTOCK_CHECKS_PER_RUN = 8


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def is_priority(name: str, keywords: list[str]) -> bool:
    lower = name.lower()
    return any(kw.lower() in lower for kw in keywords)


def is_excluded(name: str, keywords: list[str]) -> bool:
    lower = name.lower()
    return any(kw.lower() in lower for kw in keywords)


def check_category_pages(
    fetcher: Fetcher,
    notifier: NtfyNotifier,
    current_state: dict,
    config: dict,
    dry_run: bool = False,
    is_first_run: bool = False,
) -> list[Product]:
    """Fetch category pages, alert on new products, and return all products found."""
    cfg = config.get("monitoring", {})
    all_products: list[Product] = []

    for url in cfg.get("category_urls", []):
        logger.info("Fetching category page: %s", url)
        data = fetcher.get_page_data(url, with_delay=False)

        if not data:
            logger.warning("Failed to fetch category page: %s", url)
            continue

        products = parse_category_page(data)
        logger.info("Found %d products on %s", len(products), url)

        exclude = cfg.get("exclude_keywords", [])
        for product in products:
            if is_excluded(product.name, exclude):
                continue

            all_products.append(product)

            if state.is_new_product(current_state, product.id):
                if is_first_run and not product.in_stock:
                    # Silently seed state on first run — don't spam the user with
                    # notifications for products that are already sold out
                    logger.debug("First run: seeding state for %s (sold out)", product.name)
                else:
                    logger.info("NEW PRODUCT: %s (%s) — in_stock=%s", product.name, product.id, product.in_stock)
                    if not dry_run:
                        notifier.send_new_product(product.name, product.price, product.url)
                    else:
                        logger.info("[DRY RUN] Would notify: new product %s", product.name)
            else:
                prev_status = state.get_product_status(current_state, product.id)
                if prev_status == "sold_out" and product.in_stock:
                    logger.info("RESTOCK (category page): %s (%s)", product.name, product.id)
                    if not dry_run:
                        notifier.send_restock(product.name, product.price, product.url)
                    else:
                        logger.info("[DRY RUN] Would notify: restock %s", product.name)

            state.upsert_product(
                current_state,
                product_id=product.id,
                name=product.name,
                url=product.url,
                in_stock=product.in_stock,
                price=product.price,
            )

    return all_products


def check_priority_restocks(
    fetcher: Fetcher,
    notifier: NtfyNotifier,
    current_state: dict,
    config: dict,
    dry_run: bool = False,
) -> None:
    """
    For sold-out priority products not seen on the category page,
    check their individual product pages for restocks.
    Limited to MAX_RESTOCK_CHECKS_PER_RUN to avoid excessive requests.
    """
    cfg = config.get("monitoring", {})
    priority_keywords = cfg.get("priority_keywords", [])
    products_state = current_state.get("seen_products", {})

    candidates = [
        (pid, data)
        for pid, data in products_state.items()
        if data.get("status") == "sold_out"
        and is_priority(data.get("name", ""), priority_keywords)
        and data.get("url")
    ]

    if not candidates:
        logger.info("No sold-out priority products queued for individual restock checks")
        return

    # Sort by most recently first-seen (new popular products get priority)
    candidates.sort(key=lambda x: x[1].get("first_seen", ""), reverse=True)
    to_check = candidates[:MAX_RESTOCK_CHECKS_PER_RUN]

    logger.info(
        "Checking %d/%d priority products for restocks",
        len(to_check), len(candidates)
    )

    for pid, saved in to_check:
        name = saved.get("name", pid)
        url = saved.get("url", "")

        logger.info("Individual restock check: %s", name)
        data = fetcher.get_page_data(url)

        if not data:
            logger.warning("Failed to fetch product page for %s", name)
            continue

        product = parse_product_page(data, url)
        if product is None:
            logger.warning("Could not parse product page for %s", name)
            continue

        if product.in_stock:
            logger.info("RESTOCK DETECTED: %s", name)
            if not dry_run:
                notifier.send_restock(name, saved.get("price"), url)
            else:
                logger.info("[DRY RUN] Would notify: restock %s", name)

            state.upsert_product(
                current_state,
                product_id=pid,
                name=name,
                url=url,
                in_stock=True,
                price=saved.get("price"),
            )
        else:
            logger.debug("%s is still sold out", name)


def run_once(config: dict, dry_run: bool = False) -> None:
    """Execute one full monitoring cycle."""
    cfg = config.get("monitoring", {})
    fetcher_cfg = config.get("fetcher", {})
    ntfy_cfg = config.get("ntfy", {})

    ntfy_topic = os.environ.get("NTFY_TOPIC") or ntfy_cfg.get("topic", "")
    ntfy_server = os.environ.get("NTFY_SERVER") or ntfy_cfg.get("server", "https://ntfy.sh")
    notifier = NtfyNotifier(topic=ntfy_topic, server=ntfy_server)

    current_state = state.load()

    data_dir = os.environ.get("PLAYWRIGHT_DATA_DIR") or DEFAULT_DATA_DIR
    headless = os.environ.get("PLAYWRIGHT_HEADLESS", "false").lower() == "true"

    fetcher = Fetcher(
        data_dir=data_dir,
        headless=headless,
        timeout=fetcher_cfg.get("timeout", 30),
        max_retries=fetcher_cfg.get("max_retries", 3),
        retry_delay=fetcher_cfg.get("retry_delay", 5),
        delay_min=cfg.get("request_delay_min", 1.5),
        delay_max=cfg.get("request_delay_max", 3.5),
    )

    is_first_run = len(current_state.get("seen_products", {})) == 0

    try:
        check_category_pages(fetcher, notifier, current_state, config, dry_run, is_first_run)
        check_priority_restocks(fetcher, notifier, current_state, config, dry_run)
    finally:
        fetcher.close()
        state.save(current_state)
        logger.info("Cycle complete")


def setup_session(config: dict) -> None:
    """
    Interactive one-time setup: opens a visible browser to establish DataDome/Incapsula
    cookies in the persistent session directory. Run this before scheduling the monitor.
    """
    cfg = config.get("monitoring", {})
    fetcher_cfg = config.get("fetcher", {})
    data_dir = os.environ.get("PLAYWRIGHT_DATA_DIR") or DEFAULT_DATA_DIR

    logger.info("Opening browser for session setup. Please wait for the page to load fully...")
    logger.info("Session will be stored in: %s", data_dir)

    fetcher = Fetcher(
        data_dir=data_dir,
        headless=False,  # Must be visible for setup
        timeout=60,
    )

    try:
        url = cfg.get("category_urls", ["https://www.pokemoncenter.com/category/tcg-cards"])[0]
        data = fetcher.get_page_data(url, with_delay=False)
        if data:
            from parser import parse_category_page
            products = parse_category_page(data)
            logger.info(
                "Session setup successful! Found %d products. "
                "The monitor will now work without showing a browser window.",
                len(products)
            )
        else:
            logger.error(
                "Session setup failed — the page did not load. "
                "Try running --setup again, or check your internet connection."
            )
    finally:
        fetcher.close()


def main():
    parser = argparse.ArgumentParser(description="Pokemon Center TCG Drop Monitor")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Log but do not send notifications")
    parser.add_argument("--test-notify", action="store_true", help="Send a test notification and exit")
    parser.add_argument("--setup", action="store_true", help="Warm up browser session (run once before scheduling)")
    parser.add_argument(
        "--loop",
        type=int,
        metavar="MINUTES",
        nargs="?",
        const=5,
        help="Run continuously every N minutes (default: 5)",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    if args.test_notify:
        ntfy_cfg = config.get("ntfy", {})
        topic = os.environ.get("NTFY_TOPIC") or ntfy_cfg.get("topic", "")
        server = os.environ.get("NTFY_SERVER") or ntfy_cfg.get("server", "https://ntfy.sh")
        notifier = NtfyNotifier(topic=topic, server=server)
        success = notifier.send_test()
        sys.exit(0 if success else 1)

    if args.setup:
        setup_session(config)
        return

    if args.loop is not None:
        interval_s = args.loop * 60
        logger.info("Running in loop mode — every %d min", args.loop)
        while True:
            try:
                run_once(config, dry_run=args.dry_run)
            except Exception as e:
                logger.error("Unexpected error in run cycle: %s", e, exc_info=True)
            logger.info("Sleeping %d minutes until next check...", args.loop)
            time.sleep(interval_s)
    else:
        run_once(config, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
