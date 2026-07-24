import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

BASE_URL = "https://www.pokemoncenter.com"


@dataclass
class Product:
    id: str          # Pokemon Center product code, e.g. "10-10447-111"
    name: str
    url: str
    price: Optional[str] = None
    in_stock: bool = False
    image_url: Optional[str] = None
    category: Optional[str] = None


def slugify(name: str) -> str:
    normalized = unicodedata.normalize("NFD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")


def build_url(code: str, name: str) -> str:
    return f"{BASE_URL}/product/{code}/{slugify(name)}"


def parse_category_page(data: dict) -> list[Product]:
    """
    Extract products from the __NEXT_DATA__ JSON of a category/search page.
    Returns a list of Product objects.
    """
    try:
        raw_products = (
            data["props"]["initialState"]["search"]["results"]["products"]
        )
    except (KeyError, TypeError) as e:
        logger.warning("Could not find products in __NEXT_DATA__: %s", e)
        return []

    products = []
    for item in raw_products:
        product = _parse_product(item)
        if product:
            products.append(product)
    return products


def _parse_product(item: dict) -> Optional[Product]:
    try:
        code = item.get("code", "").strip()
        name = item.get("name", "").strip()
        if not code or not name:
            return None

        out_of_stock = item.get("outOfStock", True)
        in_stock = not out_of_stock

        # Price
        price = None
        price_obj = item.get("listPrice") or item.get("purchasePrice")
        if isinstance(price_obj, dict):
            price = price_obj.get("display") or (
                f"${float(price_obj['amount']):.2f}" if price_obj.get("amount") is not None else None
            )

        # Image
        image_url = None
        images = item.get("images")
        if isinstance(images, list) and images:
            image_url = images[0].get("thumbnail") or images[0].get("original")

        # Category (last segment of the reporting crumb)
        category = None
        crumb = item.get("reportingCrumb", "")
        if crumb and ">" in crumb:
            category = crumb.split(">")[-1].strip()

        return Product(
            id=code,
            name=name,
            url=build_url(code, name),
            price=price,
            in_stock=in_stock,
            image_url=image_url,
            category=category,
        )
    except Exception as e:
        logger.debug("Failed to parse product: %s", e)
        return None


def parse_product_page(data: dict, product_url: str = "") -> Optional[Product]:
    """
    Extract stock status from the __NEXT_DATA__ JSON of an individual product page.
    Product pages store data at initialState.product with an `availability` string field.
    """
    try:
        initial = data.get("props", {}).get("initialState", {})
        item = initial.get("product")
        if not isinstance(item, dict) or not item.get("code"):
            return None

        code = item.get("code", "").strip()
        name = item.get("name", "").strip()
        if not code or not name:
            return None

        # Product pages use "availability" string instead of "outOfStock" boolean
        availability = item.get("availability", "")
        in_stock = availability.upper() in ("AVAILABLE", "IN_STOCK", "PURCHASABLE")

        price = None
        price_obj = item.get("listPrice") or item.get("purchasePrice")
        if isinstance(price_obj, dict):
            price = price_obj.get("display") or (
                f"${float(price_obj['amount']):.2f}" if price_obj.get("amount") is not None else None
            )

        url = product_url or build_url(code, name)

        return Product(
            id=code,
            name=name,
            url=url,
            price=price,
            in_stock=in_stock,
        )
    except Exception as e:
        logger.warning("Failed to parse product page: %s", e)
        return None
