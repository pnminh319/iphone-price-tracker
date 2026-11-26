import json
import re
import time
import requests

from bs4 import BeautifulSoup
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

VERSION = "V5.0"

USER_AGENT = "iPhonePriceTracker/1.0"

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": USER_AGENT,
    "Accept-Language": "vi-VN,vi;q=0.9",
    "Accept": "text/html,application/xhtml+xml"
})

ROBOTS_CACHE = {}

RETAILER_DOMAINS = {
    "CPS": "cellphones.com.vn",
    "HHM": "hoanghamobile.com",
    "DDV": "didongviet.vn",
    "VTS": "viettelstore.vn",
    "SDK": "shopdunk.com",
    "PV": "phongvu.vn", "MTM": "minhtuanmobile.com", "TZ": "topzone.vn", "H24": "24hstore.vn",
    "TGDD": "thegioididong.com",
    "FPT": "fptshop.com.vn"
}


# ========================================
# NORMALIZE PRICE
# ========================================

def normalize_price(value):

    if value is None:
        return None

    if isinstance(value, (int, float)):
        price = int(value)

    else:

        text = str(value).strip()

        if re.fullmatch(r"\d+(?:\.0+)?", text):
            price = int(float(text))

        else:

            match = re.fullmatch(
                r"(\d{1,3}(?:[.,]\d{3})+)"
                r"\s*(?:đ|₫|VND|VNĐ)?",
                text,
                re.IGNORECASE
            )

            if not match:
                return None

            price = int(
                re.sub(r"\D", "", match.group(1))
            )

    if 1000000 <= price <= 150000000:
        return price

    return None


# ========================================
# ROBOTS
# ========================================

def check_robots(url):

    parsed = urlparse(url)

    origin = f"{parsed.scheme}://{parsed.netloc}"

    if origin not in ROBOTS_CACHE:

        robots_url = origin + "/robots.txt"

        try:

            response = SESSION.get(
                robots_url,
                timeout=(15, 25),
                allow_redirects=False
            )

            if response.status_code in (404, 410):

                ROBOTS_CACHE[origin] = True

            elif response.status_code == 200:

                parser = RobotFileParser()

                parser.parse(
                    response.text.splitlines()
                )

                ROBOTS_CACHE[origin] = parser

            else:

                print(
                    "ROBOTS_UNAVAILABLE:",
                    response.status_code,
                    origin
                )

                ROBOTS_CACHE[origin] = False

        except requests.RequestException as e:

            print("ROBOTS_ERROR:", str(e))

            ROBOTS_CACHE[origin] = False

    rule = ROBOTS_CACHE[origin]

    if isinstance(rule, bool):
        return rule

    return rule.can_fetch(USER_AGENT, url)


# ========================================
# FETCH HTML
# ========================================

def fetch_html(url):

    if not check_robots(url):
        return None

    try:

        time.sleep(1.5)

        response = SESSION.get(
            url,
            timeout=(15, 40),
            allow_redirects=False
        )

        if response.status_code != 200:

            print(
                "HTTP_ERROR:",
                response.status_code,
                url
            )

            return None

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        title = (
            soup.title.get_text(strip=True).lower()
            if soup.title else ""
        )

        if any(x in title for x in [
            "access denied",
            "verify you are human",
            "captcha",
            "just a moment"
        ]):

            return None

        return soup

    except requests.RequestException as e:

        print("FETCH_ERROR:", str(e))

        return None


# ========================================
# JSON LD
# ========================================

def walk_json(data):

    if isinstance(data, dict):

        yield data

        for value in data.values():
            yield from walk_json(value)

    elif isinstance(data, list):

        for item in data:
            yield from walk_json(item)


def get_product_offers(soup):

    products = []

    for script in soup.select(
        'script[type="application/ld+json"]'
    ):

        try:

            data = json.loads(script.get_text())

        except (ValueError, TypeError):
            continue

        for node in walk_json(data):

            types = node.get("@type", [])

            if isinstance(types, str):
                types = [types]

            if "Product" in types:
                products.append(node)

    # Do not guess when several products are present.
    if len(products) != 1:
        return []

    offers = products[0].get("offers", [])

    if isinstance(offers, dict):
        offers = [offers]

    return [
        offer for offer in offers
        if isinstance(offer, dict)
        and offer.get("priceCurrency") == "VND"
    ]


def get_jsonld_price(soup):

    offers = get_product_offers(soup)

    prices = set()
    stocks = set()

    for offer in offers:

        price = normalize_price(
            offer.get("price")
        )

        if price:
            prices.add(price)

        availability = str(
            offer.get("availability", "")
        ).lower().split("/")[-1]

        if availability == "instock":
            stocks.add("IN_STOCK")

        elif availability in (
            "outofstock",
            "soldout",
            "discontinued"
        ):
            stocks.add("OUT_OF_STOCK")

        elif availability in (
            "preorder",
            "presale"
        ):
            stocks.add("PREORDER")

    price = (
        next(iter(prices))
        if len(prices) == 1 else None
    )

    stock = (
        next(iter(stocks))
        if len(stocks) == 1 else "UNKNOWN"
    )

    return price, stock


# ========================================
# SELECTOR HELPERS
# ========================================

def node_price(node):

    if node is None:
        return None

    value = (
        node.get("content")
        or node.get_text(" ", strip=True)
    )

    return normalize_price(value)


def unique_price(root, selector):

    prices = {
        node_price(node)
        for node in root.select(selector)
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

    for current in soup.select(current_selector):

        if node_price(current) != price:
            continue

        parent = current.parent

        # Only inspect nearby price containers.
        for _ in range(3):

            if parent is None:
                break

            if parent.name in ("body", "html"):
                break

            if len(
                parent.get_text(" ", strip=True)
            ) > 350:
                break

            old_nodes = parent.select(old_selector)

            if old_nodes:

                values = {
                    node_price(node)
                    for node in old_nodes
                }

                values.discard(None)

                if len(values) == 1:

                    old = next(iter(values))

                    if old > price:
                        candidates.add(old)

                break

            parent = parent.parent

    if len(candidates) == 1:
        return next(iter(candidates))

    return None


# ========================================
# CELLPHONES
# ========================================

def read_cps(soup):

    price, stock = get_jsonld_price(soup)

    selector = (
        "p.font-bold.text-neutral-800.text-xl"
    )

    html_price = unique_price(
        soup,
        selector
    )

    if price and html_price and price != html_price:
        return None, "PRICE_CONFLICT", None

    price = price or html_price

    original = paired_old_price(
        soup,
        selector,
        "del.text-neutral-400",
        price
    )

    return price, stock, original


# ========================================
# HOANG HA MOBILE
# ========================================

def read_hhm(soup):

    price, stock = get_jsonld_price(soup)

    # Do not use recommended-product prices.
    # Do not use trade-in or member prices.

    original = paired_old_price(
        soup,
        "[data-product-price]",
        "[data-product-last-price]",
        price
    )

    return price, stock, original


# ========================================
# DI DONG VIET
# ========================================

def read_ddv(soup):

    json_price, stock = get_jsonld_price(soup)

    selector = (
        "div.text-2xl.font-bold.text-gray-900"
    )

    price = unique_price(
        soup,
        selector
    )

    if json_price and price and json_price != price:
        return None, "PRICE_CONFLICT", None

    price = price or json_price

    original = paired_old_price(
        soup,
        selector,
        "span.text-sm.text-gray-500.line-through",
        price
    )

    return price, stock, original


# ========================================
# VIETTEL STORE
# ========================================

def read_vts(soup):

    json_price, stock = get_jsonld_price(soup)

    price = unique_price(
        soup,
        ".block-box-price .price"
    )

    if json_price and price and json_price != price:
        return None, "PRICE_CONFLICT", None

    price = price or json_price

    original = paired_old_price(
        soup,
        ".block-box-price .price",
        ".price-old",
        price
    )

    return price, stock, original


# ========================================
# GENERIC READER
# TGDD / FPT
# ========================================

def read_sdk(soup):
    root = soup.select_one('.product-essential .overview:has(.box-product-price-wrapper)')
    if root is None:
        return None, "UNKNOWN", None
    price = unique_price(root, '.box-product-price-wrapper .price-flex .product-price')
    original = unique_price(root, '.box-product-price-wrapper .price-flex .old-product-price')
    def visible(node):
        return not any('d-none' in p.get('class', []) or p.has_attr('hidden') or 'display:none' in p.get('style', '').replace(' ', '').lower() for p in [node, *node.parents])
    buy = any(visible(b) and not b.has_attr('disabled') for b in root.select('.first-add-to-cart button.once-submit-button'))
    unavailable = any(visible(b) for b in root.select('.add-to-cart-subscriber button'))
    stock = 'IN_STOCK' if buy and not unavailable else 'OUT_OF_STOCK' if unavailable and not buy else 'UNKNOWN'
    return price, stock, original if price and original and original > price else None


def read_generic(soup):

    price, stock = get_jsonld_price(soup)

    # Original price is intentionally left empty
    # until a retailer-specific selector is verified.

    return price, stock, None


def read_new(soup, retailer):
    selectors = {
        "PV": (".css-1v2ddkd", None, "button.css-jbfqts"),
        "MTM": (".prodetail__price--buynow b.price", ".prodetail__price--buynow s", ".btn-pdetail-muangay"),
        "TZ": ("strong.price.box_normal", None, ".btn-buy.full"),
        "H24": (".sticky_price_current", ".sticky_price_old", ".sticky_btn_buy"),
    }
    current, old, buy = selectors[retailer]
    price = unique_price(soup, current)
    original = unique_price(soup, old) if old else None
    def visible(node):
        return not any(p.has_attr('hidden') or p.has_attr('disabled') or p.get('aria-disabled') == 'true' or set(p.get('class', [])) & {'hidden','d-none','disabled'} or 'display:none' in p.get('style','').replace(' ','').lower() for p in [node,*node.parents])
    stock = 'IN_STOCK' if any(visible(n) and 'mua ngay' in n.get_text(' ',strip=True).lower() for n in soup.select(buy)) else 'UNKNOWN'
    return price, stock, original if price and original and original > price else None

READERS = {
    **{r: (lambda soup, r=r: read_new(soup,r)) for r in ('PV','MTM','TZ','H24')},
    "CPS": read_cps,
    "HHM": read_hhm,
    "DDV": read_ddv,
    "VTS": read_vts,
    "SDK": read_sdk,
    "TGDD": read_generic,
    "FPT": read_generic
}


# ========================================
# MAIN INTERFACE
# ========================================

def read_price(product):

    retailer = str(
        product.get("retailer", "")
    ).strip().upper()

    url = str(
        product.get("url", "")
    ).strip()

    if retailer not in READERS:
        return None, "UNKNOWN_RETAILER", None

    parsed = urlparse(url)

    host = parsed.hostname or ""

    domain = RETAILER_DOMAINS[retailer]

    if (
        parsed.scheme != "https"
        or not (
            host == domain
            or host.endswith("." + domain)
        )
    ):

        return None, "INVALID_URL", None

    print(
        f"[{VERSION}]",
        retailer,
        url
    )

    soup = fetch_html(url)

    if soup is None:
        return None, "FETCH_FAILED", None

    # A URL may represent multiple colors.
    # Do not assign its default price to a named
    # color without verified variant mapping.
    color = str(
        product.get("color", "")
    ).strip().lower()

    if color not in (
        "",
        "all",
        "default",
        "unknown",
        "n/a",
        "none",
        "any",
        "tất cả",
        "chưa xác định"
    ):

        print("COLOR_NOT_VERIFIED:", color)

        return None, "VARIANT_UNVERIFIED", None

    try:

        if retailer in {"PV","MTM","TZ","H24"}:
            from backfill_history import identity
            title = soup.select_one('h1')
            expected = identity(str(product.get('model',''))+' '+str(product.get('storage','')))
            if not title or not expected or identity(title.get_text(' ',strip=True)) != expected:
                return None, "VARIANT_UNVERIFIED", None
        if retailer == "SDK":
            from backfill_history import identity
            title = soup.select_one(".product-essential .overview:has(.box-product-price-wrapper) .product-name")
            expected = identity(str(product.get("model", "")) + " " + str(product.get("storage", "")))
            if not title or not expected or identity(title.get_text(" ", strip=True)) != expected:
                return None, "VARIANT_UNVERIFIED", None

        price, stock, original = READERS[
            retailer
        ](soup)

        if not price:

            print(
                "PRICE_NOT_FOUND:",
                retailer,
                url
            )

            return None, stock, None

        if original and original <= price:
            original = None

        print(
            "PRICE_OK:",
            retailer,
            "PRICE =", price,
            "ORIGINAL =", original,
            "STOCK =", stock
        )

        return price, stock, original

    except Exception as e:

        print(
            "READER_ERROR:",
            retailer,
            str(e)
        )

        return None, "ERROR", None
