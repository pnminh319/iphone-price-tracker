
import json
import re

from bs4 import BeautifulSoup


VERSION = "V5.2"


def parse_money(value):
    if value is None:
        return None

    text = str(value).strip()

    if not re.fullmatch(
        r"[\d.,\s]+[đ₫]?", text
    ):
        return None

    digits = re.sub(r"\D", "", text)

    if not digits:
        return None

    price = int(digits)

    if 1000000 <= price <= 200000000:
        return price

    return None


def get_jsonld(soup):
    result = []

    for script in soup.select(
        'script[type="application/ld+json"]'
    ):
        try:
            data = json.loads(script.get_text())

            if isinstance(data, list):
                result.extend(data)

            elif isinstance(data, dict):
                if "@graph" in data:
                    result.extend(data["@graph"])
                else:
                    result.append(data)

        except (ValueError, TypeError):
            continue

    return result


def get_text_price(node):
    if node is None:
        return None

    return parse_money(
        node.get_text("", strip=True)
    )


def stock_status(offer):
    value = str(
        offer.get("availability", "")
    ).split("/")[-1]

    mapping = {
        "InStock": "IN_STOCK",
        "OutOfStock": "OUT_OF_STOCK",
        "SoldOut": "OUT_OF_STOCK",
        "PreOrder": "PRE_ORDER",
        "BackOrder": "BACK_ORDER",
        "LimitedAvailability": "LIMITED"
    }

    return mapping.get(value, "UNKNOWN")


def get_storage(text):
    match = re.search(
        r"\b(64|128|256|512|1024|2048|1|2)"
        r"\s*(GB|TB)\b",
        str(text),
        re.I
    )

    if not match:
        return None

    size = int(match.group(1))
    unit = match.group(2).upper()

    if unit == "GB" and size in (1024, 2048):
        size //= 1024
        unit = "TB"

    return f"{size}{unit}"


def make_result(
    retailer, name, storage, color,
    price, original_price, stock, url
):
    if stock == "OUT_OF_STOCK":
        price = None

    if (
        not price
        or not original_price
        or original_price < price
    ):
        original_price = None

    saving = None
    discount = None

    if price and original_price:
        saving = original_price - price
        discount = round(
            saving / original_price * 100, 2
        )

    return {
        "retailer": retailer,
        "name": name,
        "storage": storage,
        "color": color,
        "price": price,
        "original_price": original_price,
        "advertised_discount_pct": discount,
        "saving_amount": saving,
        "stock": stock,
        "url": url
    }


def parse_tgdd(html):
    soup = BeautifulSoup(html, "html.parser")

    price_node = soup.select_one(
        "span.text-20.font-700.text-red-5"
    )

    display_price = get_text_price(price_node)

    original_price = None

    if price_node:
        original_price = get_text_price(
            price_node.parent.select_one("del")
        )

    results = []

    for item in get_jsonld(soup):

        if item.get("@type") != "ProductGroup":
            continue

        for variant in item.get("hasVariant", []):

            offer = variant.get("offers", {})

            if not isinstance(offer, dict):
                continue

            price = parse_money(
                offer.get("price")
            )

            stock = stock_status(offer)

            name = variant.get("name", "")

            storage = get_storage(
                variant.get("size", name)
            )

            color = variant.get(
                "color", "UNKNOWN"
            )

            url = offer.get(
                "url", variant.get("url", "")
            )

            # The displayed old price is not
            # necessarily valid for every color.
            old = None

            if (
                len(item.get("hasVariant", [])) == 1
                and price == display_price
            ):
                old = original_price

            results.append(
                make_result(
                    "TGDD", name, storage,
                    color, price, old,
                    stock, url
                )
            )

    # Preserve the page-level advertised price
    # separately from individual color offers.
    heading = soup.select_one("h1")
    canonical = soup.select_one(
        'link[rel="canonical"]'
    )

    if heading and display_price:
        name = heading.get_text(" ", strip=True)

        results.append(
            make_result(
                "TGDD",
                name,
                get_storage(name),
                "UNKNOWN",
                display_price,
                original_price,
                "UNKNOWN",
                canonical.get("href", "")
                if canonical else ""
            )
        )

    return results


def parse_fpt(html):
    soup = BeautifulSoup(html, "html.parser")

    price_node = soup.select_one(
        "span.text-2xl.font-semibold"
        ".text-black-opacity-100"
    )

    display_price = get_text_price(price_node)

    original_price = None

    if price_node:
        original_price = get_text_price(
            price_node.parent.select_one(
                "span.line-through"
            )
        )

    heading = soup.select_one("h1")

    results = []

    for item in get_jsonld(soup):

        if item.get("@type") != "Product":
            continue

        offer = item.get("offers", {})

        if not isinstance(offer, dict):
            continue

        name = (
            heading.get_text(" ", strip=True)
            if heading
            else item.get("name", "")
        )

        price = parse_money(
            offer.get("price")
        )

        stock = stock_status(offer)

        # Reject conflicting structured and
        # displayed prices for manual review.
        if (
            price and display_price
            and price != display_price
        ):
            raise ValueError(
                "FPT_PRICE_MISMATCH"
            )

        old = (
            original_price
            if price == display_price
            else None
        )

        results.append(
            make_result(
                "FPT",
                name,
                get_storage(name),
                "UNKNOWN",
                price,
                old,
                stock,
                offer.get("url", "")
            )
        )

    return results


def parse_html(retailer, html):
    retailer = retailer.strip().upper()

    if retailer == "TGDD":
        return parse_tgdd(html)

    if retailer == "FPT":
        return parse_fpt(html)

    raise ValueError(
        f"UNSUPPORTED_RETAILER: {retailer}"
    )
