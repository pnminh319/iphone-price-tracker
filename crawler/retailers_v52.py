
import json
import re
import unicodedata

from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit, urljoin

from bs4 import BeautifulSoup


VERSION = "V5.3"

HOSTS = {
    "TGDD": "www.thegioididong.com",
    "FPT": "fptshop.com.vn"
}

MODEL_PATTERN = re.compile(
    r"\biphone[\s-]*"
    r"(\d{1,2}e?|air|se|duo)"
    r"(?:[\s-]+"
    r"(pro[\s-]+max|pro|plus|mini|air))?"
    r"\b",
    re.I
)

STORAGE_PATTERN = re.compile(
    r"\b(64|128|256|512|1024|2048|1|2)"
    r"[\s-]*(GB|TB)\b",
    re.I
)

EXCLUDE = re.compile(
    r"máy cũ|may-cu|like.new|"
    r"refurbished|đã qua sử dụng|"
    r"đổi trả|trầy xước|"
    r"ốp lưng|cường lực|"
    r"phụ kiện|99%|98%|95%",
    re.I
)


# ==========================================
# NORMALIZE PRICE
# ==========================================

def parse_money(value):

    if value is None or isinstance(value, bool):
        return None

    text = str(value).strip()

    text = re.sub(
        r"VND|VNĐ|đ|₫",
        "",
        text,
        flags=re.I
    )

    text = re.sub(r"\s+", "", text)

    try:

        if re.fullmatch(
            r"\d+(?:[.,]0{1,2})?",
            text
        ):
            number = Decimal(
                text.replace(",", ".")
            )

        elif re.fullmatch(
            r"\d{1,3}(?:\.\d{3})+",
            text
        ):
            number = Decimal(
                text.replace(".", "")
            )

        elif re.fullmatch(
            r"\d{1,3}(?:,\d{3})+",
            text
        ):
            number = Decimal(
                text.replace(",", "")
            )

        else:
            return None

        if number != number.to_integral_value():
            return None

        price = int(number)

        if 1000000 <= price <= 100000000:
            return price

    except (InvalidOperation, ValueError):
        pass

    return None


# ==========================================
# NORMALIZE MODEL
# ==========================================

def get_model(text):

    text = str(text or "")

    if EXCLUDE.search(text):
        return None

    match = MODEL_PATTERN.search(text)

    if not match:
        return None

    model = (
        "iPhone "
        + match.group(1).title()
    )

    if match.group(2):
        model += " " + (
            match.group(2)
            .replace("-", " ")
            .title()
        )

    return model


# ==========================================
# NORMALIZE STORAGE
# ==========================================

def get_storage(text):

    matches = STORAGE_PATTERN.findall(
        str(text or "")
    )

    values = set()

    for number, unit in matches:

        size = int(number)
        unit = unit.upper()

        if unit == "GB":

            if size in (1024, 2048):
                size //= 1024
                unit = "TB"

            elif size not in (
                64, 128, 256, 512
            ):
                continue

        if unit == "TB" and size not in (1, 2):
            continue

        values.add(f"{size}{unit}")

    if len(values) == 1:
        return next(iter(values))

    return None


# ==========================================
# NORMALIZE COLOR
# ==========================================

def normalize_color(value):

    if not isinstance(value, str):
        return "UNKNOWN"

    color = re.sub(
        r"\s+",
        " ",
        value
    ).strip()

    if color.lower() in (
        "",
        "unknown",
        "default",
        "none",
        "all",
        "n/a",
        "chưa xác định"
    ):
        return "UNKNOWN"

    return color


def color_key(color):

    color = normalize_color(color)

    text = unicodedata.normalize(
        "NFKD",
        color
    )

    text = "".join(
        c for c in text
        if not unicodedata.combining(c)
    )

    text = (
        text.replace("đ", "d")
        .replace("Đ", "D")
        .upper()
    )

    text = re.sub(
        r"[^A-Z0-9]+",
        "-",
        text
    ).strip("-")

    return text or "UNKNOWN"


# ==========================================
# CREATE SKU
# ==========================================

def make_sku(model, storage, color):

    if not model or not storage:
        return None

    return "-".join([
        model.upper().replace(" ", "-"),
        storage.upper(),
        color_key(color)
    ])


# ==========================================
# VALIDATE PRODUCT URL
# ==========================================

def valid_url(retailer, url):

    if not isinstance(url, str):
        return False

    try:

        parsed = urlsplit(url)

        if (
            parsed.scheme != "https"
            or parsed.netloc.lower() != HOSTS[retailer]
            or parsed.username
            or parsed.password
        ):
            return False

        prefix = (
            "/dtdd/iphone-"
            if retailer == "TGDD"
            else "/dien-thoai/iphone-"
        )

        return parsed.path.lower().startswith(
            prefix
        )

    except ValueError:
        return False


def resolve_url(retailer, url, fallback=""):

    if not isinstance(url, str):
        url = ""

    base = f"https://{HOSTS[retailer]}/"

    candidate = urljoin(
        fallback or base,
        url
    )

    if valid_url(retailer, candidate):
        return candidate

    if valid_url(retailer, fallback):
        return fallback

    return ""


# ==========================================
# JSON-LD
# ==========================================

def walk_json(data):

    if isinstance(data, dict):

        yield data

        for value in data.values():
            yield from walk_json(value)

    elif isinstance(data, list):

        for item in data:
            yield from walk_json(item)


def get_jsonld(soup):

    result = []

    for script in soup.select(
        'script[type="application/ld+json"]'
    ):

        try:

            data = json.loads(
                script.get_text()
            )

            result.extend(
                walk_json(data)
            )

        except (ValueError, TypeError):
            continue

    return result


def has_type(item, expected):

    types = item.get("@type", [])

    if isinstance(types, str):
        types = [types]

    return (
        isinstance(types, list)
        and expected in types
    )


def as_list(value):

    if isinstance(value, dict):
        return [value]

    if isinstance(value, list):
        return [
            item for item in value
            if isinstance(item, dict)
        ]

    return []


# ==========================================
# STOCK
# ==========================================

def stock_status(offer):

    value = str(
        offer.get("availability", "")
    ).rstrip("/").split("/")[-1].lower()

    mapping = {
        "instock": "IN_STOCK",
        "outofstock": "OUT_OF_STOCK",
        "soldout": "OUT_OF_STOCK",
        "discontinued": "OUT_OF_STOCK",
        "preorder": "PREORDER",
        "presale": "PREORDER",
        "backorder": "BACKORDER",
        "limitedavailability": "LIMITED"
    }

    return mapping.get(
        value,
        "UNKNOWN"
    )


def valid_offer(offer):

    if str(
        offer.get("priceCurrency", "")
    ).upper() != "VND":
        return False

    condition = str(
        offer.get("itemCondition", "")
    ).lower()

    if any(x in condition for x in (
        "usedcondition",
        "refurbishedcondition",
        "damagedcondition"
    )):
        return False

    return True


# ==========================================
# HTML PRICE HELPERS
# ==========================================

def get_text_price(node):

    if node is None:
        return None

    return parse_money(
        node.get("content")
        or node.get_text("", strip=True)
    )


def unique_price(soup, selector):

    prices = {
        get_text_price(node)
        for node in soup.select(selector)
    }

    prices.discard(None)

    if len(prices) == 1:
        return next(iter(prices))

    return None


def paired_old_price(
    soup,
    current_selector,
    old_selector,
    price
):

    if not price:
        return None

    candidates = set()

    for node in soup.select(
        current_selector
    ):

        if get_text_price(node) != price:
            continue

        parent = node.parent

        for _ in range(3):

            if parent is None:
                break

            if parent.name in ("html", "body"):
                break

            if len(
                parent.get_text(" ", strip=True)
            ) > 350:
                break

            old_nodes = parent.select(
                old_selector
            )

            if old_nodes:

                prices = {
                    get_text_price(old)
                    for old in old_nodes
                }

                prices.discard(None)

                if len(prices) == 1:

                    old = next(iter(prices))

                    if old > price:
                        candidates.add(old)

                break

            parent = parent.parent

    if len(candidates) == 1:
        return next(iter(candidates))

    return None


# ==========================================
# PAGE INFORMATION
# ==========================================

def page_info(soup, retailer, page_url=""):

    heading = soup.select_one("h1")

    name = (
        heading.get_text(" ", strip=True)
        if heading else ""
    )

    canonical = soup.select_one(
        'link[rel="canonical"]'
    )

    canonical_url = (
        canonical.get("href", "")
        if canonical else ""
    )

    url = resolve_url(
        retailer,
        page_url or canonical_url
    )

    model = get_model(name)
    storage = get_storage(name)

    url_model = get_model(
        urlsplit(url).path.replace("-", " ")
    )

    url_storage = get_storage(
        urlsplit(url).path.replace("-", " ")
    )

    if model and url_model and model != url_model:
        raise ValueError("PAGE_MODEL_MISMATCH")

    if (
        storage
        and url_storage
        and storage != url_storage
    ):
        raise ValueError("PAGE_STORAGE_MISMATCH")

    return {
        "name": name,
        "model": model or url_model,
        "storage": storage or url_storage,
        "url": url
    }


# ==========================================
# STANDARD RESULT
# ==========================================

def make_result(
    retailer,
    name,
    storage,
    color,
    price,
    original_price,
    stock,
    url,
    scope="PRODUCT"
):

    model = get_model(name)
    color = normalize_color(color)

    price = parse_money(price)
    original_price = parse_money(
        original_price
    )

    if not model or not storage:
        return None

    if not valid_url(retailer, url):
        return None

    if stock == "OUT_OF_STOCK":
        price = None

    if not price and stock != "OUT_OF_STOCK":
        return None

    if (
        not price
        or not original_price
        or original_price <= price
    ):
        original_price = None

    saving = None
    discount = None

    if price and original_price:

        saving = original_price - price

        discount = round(
            saving / original_price * 100,
            2
        )

    return {
        "retailer": retailer,
        "sku": make_sku(
            model,
            storage,
            color
        ),
        "name": name,
        "model": model,
        "storage": storage,
        "color": color,
        "price": price,
        "original_price": original_price,
        "advertised_discount_pct": discount,
        "saving_amount": saving,
        "stock": stock,
        "url": url,
        "scope": scope
    }


# ==========================================
# DEDUPLICATE OFFERS
# ==========================================

def deduplicate(results):

    found = {}

    for row in results:

        if not row:
            continue

        key = (
            row["retailer"],
            row["sku"]
        )

        if key in found:

            previous = found[key]

            if (
                previous["price"] != row["price"]
                or previous["stock"] != row["stock"]
            ):
                raise ValueError(
                    f"CONFLICTING_OFFER: {key}"
                )

            if (
                not previous["original_price"]
                and row["original_price"]
            ):
                found[key] = row

        else:
            found[key] = row

    return sorted(
        found.values(),
        key=lambda row: (
            row["model"],
            row["storage"],
            row["color"]
        )
    )


# ==========================================
# TGDD READER
# ==========================================

def parse_tgdd(html, page_url=""):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    page = page_info(
        soup,
        "TGDD",
        page_url
    )

    selector = (
        "span.text-20.font-700.text-red-5"
    )

    display_price = unique_price(
        soup,
        selector
    )

    original_price = paired_old_price(
        soup,
        selector,
        "del",
        display_price
    )

    results = []

    for item in get_jsonld(soup):

        if not has_type(item, "ProductGroup"):
            continue

        variants = as_list(
            item.get("hasVariant")
        )

        for variant in variants:

            name = str(
                variant.get("name", "")
            ).strip()

            # URL-only variants are not offers.
            if not name:
                continue

            model = get_model(name)

            storage = (
                get_storage(
                    variant.get("size", "")
                )
                or get_storage(name)
            )

            if not model or not storage:
                continue

            if (
                page["model"]
                and model != page["model"]
            ):
                continue

            if (
                page["storage"]
                and storage != page["storage"]
            ):
                continue

            color = normalize_color(
                variant.get("color")
            )

            # Keep unnamed variants separate
            # from verified color offers.
            if color == "UNKNOWN":
                continue

            for offer in as_list(
                variant.get("offers")
            ):

                if not valid_offer(offer):
                    continue

                price = parse_money(
                    offer.get("price")
                )

                stock = stock_status(offer)

                url = resolve_url(
                    "TGDD",
                    offer.get("url")
                    or variant.get("url", ""),
                    page["url"]
                )

                if not url:
                    continue

                row = make_result(
                    "TGDD",
                    name,
                    storage,
                    color,
                    price,
                    None,
                    stock,
                    url,
                    "COLOR"
                )

                if row:
                    results.append(row)

    # Product-level price is independent
    # from the color-specific price.

    if (
        page["model"]
        and page["storage"]
        and display_price
    ):

        matching = [
            row for row in results
            if row["model"] == page["model"]
            and row["storage"] == page["storage"]
            and row["price"] == display_price
            and row["stock"] == "IN_STOCK"
        ]

        stock = (
            "IN_STOCK"
            if matching else "UNKNOWN"
        )

        name = (
            f'{page["model"]} '
            f'{page["storage"]}'
        )

        row = make_result(
            "TGDD",
            name,
            page["storage"],
            "UNKNOWN",
            display_price,
            original_price,
            stock,
            page["url"],
            "PRODUCT"
        )

        if row:
            results.append(row)

    return deduplicate(results)


# ==========================================
# FPT READER
# ==========================================

def parse_fpt(html, page_url=""):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    page = page_info(
        soup,
        "FPT",
        page_url
    )

    selector = (
        "span.text-2xl.font-semibold"
        ".text-black-opacity-100"
    )

    display_price = unique_price(
        soup,
        selector
    )

    original_price = paired_old_price(
        soup,
        selector,
        "span.line-through",
        display_price
    )

    results = []

    for item in get_jsonld(soup):

        if not has_type(item, "Product"):
            continue

        item_name = str(
            item.get("name", "")
        ).strip()

        model = get_model(item_name)

        storage = (
            get_storage(item_name)
            or get_storage(
                item.get("size", "")
            )
        )

        if not model:
            continue

        if (
            page["model"]
            and model != page["model"]
        ):
            continue

        if (
            storage
            and page["storage"]
            and storage != page["storage"]
        ):
            continue

        storage = storage or page["storage"]

        if not storage:
            continue

        for offer in as_list(
            item.get("offers")
        ):

            if not valid_offer(offer):
                continue

            price = parse_money(
                offer.get("price")
            )

            stock = stock_status(offer)

            if (
                stock != "OUT_OF_STOCK"
                and price
                and display_price
                and price != display_price
            ):
                raise ValueError(
                    "FPT_PRICE_MISMATCH"
                )

            url = resolve_url(
                "FPT",
                offer.get("url", ""),
                page["url"]
            )

            old = (
                original_price
                if price == display_price
                else None
            )

            name = f"{model} {storage}"

            row = make_result(
                "FPT",
                name,
                storage,
                "UNKNOWN",
                price,
                old,
                stock,
                url,
                "PRODUCT"
            )

            if row:
                results.append(row)

    return deduplicate(results)


# ==========================================
# PUBLIC INTERFACE
# ==========================================

def parse_html(
    retailer,
    html,
    page_url=""
):

    retailer = retailer.strip().upper()

    if retailer == "TGDD":
        return parse_tgdd(
            html,
            page_url
        )

    if retailer == "FPT":
        return parse_fpt(
            html,
            page_url
        )

    raise ValueError(
        f"UNSUPPORTED_RETAILER: {retailer}"
    )
