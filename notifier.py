import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class NtfyNotifier:
    def __init__(self, topic: str, server: str = "https://ntfy.sh"):
        self.topic = topic
        self.server = server.rstrip("/")

    @property
    def url(self) -> str:
        return f"{self.server}/{self.topic}"

    def send(
        self,
        title: str,
        body: str,
        priority: str = "high",
        click_url: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> bool:
        """
        Send a push notification via ntfy.sh.
        Returns True on success, False on failure.
        """
        if not self.topic:
            logger.error("NTFY_TOPIC is not set — cannot send notification")
            return False

        headers = {
            "Title": title,
            "Priority": priority,
            "Content-Type": "text/plain",
        }
        if click_url:
            headers["Click"] = click_url
        if tags:
            headers["Tags"] = ",".join(tags)

        try:
            response = requests.post(
                self.url,
                data=body.encode("utf-8"),
                headers=headers,
                timeout=10,
            )
            if response.status_code == 200:
                logger.info("Notification sent: %s", title)
                return True
            else:
                logger.error(
                    "ntfy.sh returned %d: %s", response.status_code, response.text
                )
                return False
        except requests.RequestException as e:
            logger.error("Failed to send ntfy notification: %s", e)
            return False

    def send_new_product(self, name: str, price: Optional[str], url: str) -> bool:
        price_str = f" — {price}" if price else ""
        return self.send(
            title=f"New Drop: {name}",
            body=f"{name}{price_str}\n\nNew TCG product just appeared on Pokemon Center!",
            priority="high",
            click_url=url,
            tags=["pokemon", "new", "tada"],
        )

    def send_restock(self, name: str, price: Optional[str], url: str) -> bool:
        price_str = f" — {price}" if price else ""
        return self.send(
            title=f"Back in Stock: {name}",
            body=f"{name}{price_str}\n\nPreviously sold-out item is now available!",
            priority="urgent",
            click_url=url,
            tags=["pokemon", "restock", "rotating_light"],
        )

    def send_test(self) -> bool:
        return self.send(
            title="Pokemon Monitor Active",
            body="Your Pokemon Center TCG monitor is running correctly.",
            priority="default",
            tags=["pokemon", "white_check_mark"],
        )


def build_notifier_from_env() -> NtfyNotifier:
    """Build a NtfyNotifier from environment variables."""
    topic = os.environ.get("NTFY_TOPIC", "")
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
    return NtfyNotifier(topic=topic, server=server)
