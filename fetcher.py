import os
import re
import time
import logging
import unicodedata
from typing import Optional

from playwright.sync_api import sync_playwright, BrowserContext

logger = logging.getLogger(__name__)

# Persistent session directory — preserved between runs so DataDome cookies stay valid
DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".playwright-data")

# How long to wait for the page to pass bot checks (DataDome, Incapsula, Queue-it)
BOT_CHECK_TIMEOUT_S = 30

# Strings that indicate the page is still a bot-check challenge page
BOT_CHECK_SIGNALS = [
    "datadome",
    "captcha-delivery.com",
    "_Incapsula_Resource",
    "Access is temporarily restricted",
    "Pardon Our Interruption",
]


def slugify(name: str) -> str:
    """Convert a product name to a URL-safe slug."""
    # Normalize unicode (é → e, etc.)
    normalized = unicodedata.normalize("NFD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    # Lowercase, replace non-alphanumeric with hyphens
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug


def build_product_url(code: str, name: str) -> str:
    """Construct a Pokemon Center product URL from code and name."""
    return f"https://www.pokemoncenter.com/product/{code}/{slugify(name)}"


class Fetcher:
    def __init__(
        self,
        data_dir: str = DEFAULT_DATA_DIR,
        headless: bool = False,
        timeout: int = 30,
        max_retries: int = 3,
        retry_delay: float = 5.0,
        delay_min: float = 1.5,
        delay_max: float = 3.5,
    ):
        self.data_dir = data_dir
        self.headless = headless
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.delay_min = delay_min
        self.delay_max = delay_max
        self._pw = None
        self._context: Optional[BrowserContext] = None

        os.makedirs(data_dir, exist_ok=True)

    def _init(self):
        """Lazy-init Playwright with a persistent Chrome context."""
        if self._context is not None:
            return

        self._pw = sync_playwright().__enter__()

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--window-size=1920,1080",
        ]
        if self.headless:
            launch_args.append("--disable-gpu")
        else:
            # Off-screen on Mac; Xvfb handles this on Linux CI
            launch_args.append("--window-position=-10000,-10000")

        try:
            self._context = self._pw.chromium.launch_persistent_context(
                user_data_dir=self.data_dir,
                channel="chrome",
                headless=self.headless,
                args=launch_args,
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
                timezone_id="America/New_York",
            )
            logger.info("Browser initialized (channel=chrome, headless=%s)", self.headless)
        except Exception as e:
            logger.warning("Chrome not found (%s) — falling back to Chromium", e)
            self._context = self._pw.chromium.launch_persistent_context(
                user_data_dir=self.data_dir,
                headless=True,  # Chromium fallback must be headless on CI
                args=launch_args,
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
                timezone_id="America/New_York",
            )
            logger.info("Browser initialized (channel=chromium, headless=True)")

    def _is_bot_blocked(self, html: str) -> bool:
        """Return True if the page content is a bot-detection challenge."""
        lower = html.lower()
        return any(signal.lower() in lower for signal in BOT_CHECK_SIGNALS)

    def get_page_data(self, url: str, with_delay: bool = True) -> Optional[dict]:
        """
        Navigate to `url` and return the parsed __NEXT_DATA__ JSON, or None on failure.
        Waits up to BOT_CHECK_TIMEOUT_S for bot checks to clear.
        """
        import random, json

        if with_delay:
            time.sleep(random.uniform(self.delay_min, self.delay_max))

        self._init()

        for attempt in range(1, self.max_retries + 1):
            page = None
            try:
                page = self._context.new_page()
                response = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)

                if response is None or response.status not in (200, 304):
                    logger.warning("HTTP %s on %s (attempt %d)", response.status if response else "?", url, attempt)
                    page.close()
                    time.sleep(self.retry_delay)
                    continue

                # Wait for __NEXT_DATA__ to appear (bot checks may delay this)
                logger.debug("Waiting for page to load: %s", url)
                got_data = False
                for _ in range(BOT_CHECK_TIMEOUT_S):
                    time.sleep(1)
                    exists = page.evaluate("() => !!document.getElementById('__NEXT_DATA__')")
                    if exists:
                        got_data = True
                        break

                if not got_data:
                    html = page.content()
                    if self._is_bot_blocked(html):
                        logger.warning("Bot detection active on %s (attempt %d/%d)", url, attempt, self.max_retries)
                    else:
                        logger.warning("__NEXT_DATA__ never appeared on %s (attempt %d/%d)", url, attempt, self.max_retries)
                    page.close()
                    time.sleep(self.retry_delay * attempt)
                    continue

                data = page.evaluate(
                    "() => JSON.parse(document.getElementById('__NEXT_DATA__').innerText)"
                )
                page.close()
                return data

            except Exception as e:
                logger.warning("Error fetching %s (attempt %d/%d): %s", url, attempt, self.max_retries, e)
                if page:
                    try:
                        page.close()
                    except Exception:
                        pass
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)

        logger.error("All %d attempts failed for %s", self.max_retries, url)
        return None

    def close(self):
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
        if self._pw:
            try:
                self._pw.__exit__(None, None, None)
            except Exception:
                pass
            self._pw = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
