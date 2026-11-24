
import os
import json
import time
import re

from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
import gspread

from bs4 import BeautifulSoup


# ==========================================
# CONFIGURATION
# ==========================================

VN_TZ = timezone(timedelta(hours=7))

USER_AGENT = "iPhonePriceTracker/1.0"

DOMAINS = {
    "TGDD": "thegioididong.com",
    "FPT": "fptshop.com.vn",
    "CPS": "cellphones.com.vn",
    "HHM": "hoanghamobile.com",
    "DDV": "didongviet.vn",
    "VTS": "viettelstore.vn"
}

HEADERS = [
    "timestamp",
    "retailer",
    "sku",
    "model",
    "storage",
    "color",
    "price",
    "baseline_price",
    "discount_pct",
    "stock",
    "url",
    "original_price",
    "advertised_discount_pct",
    "saving_amount"
]

LOG_HEADERS = [
    "timestamp",
    "retailer",
    "url",
    "status"
]

session = requests.Session()

session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept-Language": "vi-VN,vi;q=0.9"
})

robots_cache = {}


# ==========================================
# CURRENT TIME
# ==========================================

def now():

    return datetime.now(VN_TZ).isoformat(
        timespec="seconds"
    )


# ==========================================
# GOOGLE SHEETS CONNECTION
# ==========================================

def get_book():

    credentials = json.loads(
        os.environ["GOOGLE_CREDENTIALS"]
    )

    client = gspread.service_account_from_dict(
        credentials,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets"
        ]
    )

    return client.open_by_key(
        os.environ["SPREADSHEET_ID"].strip()
    )


# ==========================================
# AUTO UPDATE SHEET HEADERS
# ==========================================

def ensure_headers(ws, headers):

    existing = ws.row_values(1)

    if existing == headers:
        return

    # Allow migration from old headers
    # without deleting historical data.

    if existing and existing != headers[:len(existing)]:

        raise ValueError(
            f"INVALID_HEADERS: {ws.title}"
        )

    if ws.col_count < len(headers):

        ws.add_cols(
            len(headers) - ws.col_count
        )

    ws.update(
        range_name="A1",
        values=[headers],
        value_input_option="RAW"
    )

    print(
        "HEADERS UPDATED:",
        ws.title
    )


# ==========================================
# NORMALIZE PRICE
# ==========================================

def normalize_price(value):

    if value is None:
        return None

    value = str(value).strip()

    try:

        if re.fullmatch(
            r"\d+(\.\d{1,2})?",
            value
        ):

            price = int(float(value))

        else:

            price = int(
                re.sub(r"\D", "", value)
            )

        if 1000000 <= price <= 100000000:
            return price

    except (ValueError, TypeError):

        pass

    return None


# ==========================================
# WALK JSON DATA
# ==========================================

def walk_json(data):

    if isinstance(data, dict):

        yield data

        for value in data.values():

            yield from walk_json(value)

    elif isinstance(data, list):

        for item in data:

            yield from walk_json(item)


# ==========================================
# READ JSON-LD PRICE
# ==========================================

def get_jsonld_price(soup, product):

    candidates = set()

    for script in soup.select(
        'script[type="application/ld+json"]'
    ):

        try:

            data = json.loads(
                script.get_text()
            )

        except (ValueError, TypeError):

            continue

        for node in walk_json(data):

            types = node.get("@type", [])

            if isinstance(types, str):
                types = [types]

            if "Product" not in types:
                continue

            name = str(
                node.get("name", "")
            ).lower()

            model = str(
                product["model"]
            ).strip().lower()

            storage = str(
                product["storage"]
            ).lower().replace(" ", "")

            compact_name = re.sub(
                r"\s+", "", name
            )

            if model not in name:
                continue

            suffix = name.split(model, 1)[1]

            if re.match(
                r"\s*(pro|plus|max|air|mini)",
                suffix
            ):
                continue

            if storage not in compact_name:
                continue

            if any(x in name for x in [
                "cũ",
                "like new",
                "đã qua sử dụng",
                "refurbished",
                "trầy xước"
            ]):

                continue

            offers = node.get("offers", [])

            if isinstance(offers, dict):

                offers = [offers]

            for offer in offers:

                if not isinstance(offer, dict):
                    continue

                if offer.get("@type") != "Offer":
                    continue

                if offer.get("priceCurrency") != "VND":
                    continue

                condition = str(
                    offer.get("itemCondition", "")
                )

                if any(x in condition for x in [
                    "UsedCondition",
                    "RefurbishedCondition",
                    "DamagedCondition"
                ]):

                    continue

                price = normalize_price(
                    offer.get("price")
                )

                stock = str(
                    offer.get(
                        "availability",
                        "UNKNOWN"
                    )
                ).split("/")[-1]

                if price:

                    candidates.add(
                        (price, stock)
                    )

    if len(candidates) == 1:

        return candidates.pop()

    if len(candidates) > 1:

        raise ValueError(
            "AMBIGUOUS_PRICE"
        )

    raise ValueError(
        "PRICE_NOT_FOUND"
    )


# ==========================================
# VALIDATE WEBSITE URL
# ==========================================

def check_url(retailer, url):

    if retailer not in DOMAINS:

        raise ValueError(
            "UNKNOWN_RETAILER"
        )

    domain = DOMAINS[retailer]

    parsed = urlparse(url)

    host = (
        parsed.hostname or ""
    ).lower()

    if parsed.scheme != "https":

        raise ValueError(
            "HTTPS_REQUIRED"
        )

    if (
        host != domain
        and not host.endswith("." + domain)
    ):

        raise ValueError(
            "INVALID_RETAILER_URL"
        )


# ==========================================
# CHECK ROBOTS.TXT
# ==========================================

def check_robots(url):

    parsed = urlparse(url)

    origin = (
        f"{parsed.scheme}://{parsed.netloc}"
    )

    if origin not in robots_cache:

        response = session.get(
            origin + "/robots.txt",
            timeout=20
        )

        if response.status_code in (404, 410):

            robots_cache[origin] = None

        else:

            response.raise_for_status()

            parser = RobotFileParser()

            parser.parse(
                response.text.splitlines()
            )

            robots_cache[origin] = parser

    parser = robots_cache[origin]

    if parser and not parser.can_fetch(
        USER_AGENT,
        url
    ):

        raise ValueError(
            "ROBOTS_DISALLOWED"
        )


# ==========================================
# PRODUCT KEY
# ==========================================

def get_key(retailer, sku):

    return (
        str(retailer).strip().upper(),
        str(sku).strip()
    )


# ==========================================
# ACTIVE STATUS
# ==========================================

def active(value):

    return str(value).strip().lower() in [
        "true",
        "1",
        "yes"
    ]


# ==========================================
# MAIN CRAWLER
# ==========================================

def main():

    from price_reader import read_price

    print("==============================")
    print("IPHONE PRICE TRACKER V4")
    print("==============================")

    book = get_book()

    products_ws = book.worksheet(
        "PRODUCTS"
    )

    current_ws = book.worksheet(
        "CURRENT_PRICE"
    )

    history_ws = book.worksheet(
        "PRICE_HISTORY"
    )

    log_ws = book.worksheet(
        "CRAWL_LOG"
    )

    # Automatically upgrade old sheet headers.

    ensure_headers(
        current_ws,
        HEADERS
    )

    ensure_headers(
        history_ws,
        HEADERS
    )

    ensure_headers(
        log_ws,
        LOG_HEADERS
    )

    products = products_ws.get_all_records()

    old_current = current_ws.get_all_records()

    current = {
        get_key(
            row["retailer"],
            row["sku"]
        ): row
        for row in old_current
    }

    history = history_ws.get_all_records()

    baselines = {}

    # Preserve first observed historical price.

    for row in history:

        key = get_key(
            row["retailer"],
            row["sku"]
        )

        price = (
            normalize_price(
                row.get("baseline_price")
            )
            or normalize_price(
                row.get("price")
            )
        )

        if price:

            baselines.setdefault(
                key,
                price
            )

    # Preserve existing baseline if available.

    for key, row in current.items():

        price = (
            normalize_price(
                row.get("baseline_price")
            )
            or normalize_price(
                row.get("price")
            )
        )

        if price:

            baselines.setdefault(
                key,
                price
            )

    history_rows = []
    log_rows = []
    active_keys = set()

    success = 0
    failed = 0
    skipped = 0

    active_products = [
        product
        for product in products
        if active(product.get("active"))
        and str(
            product.get("model", "")
        ).strip().lower().startswith("iphone")
    ]

    print(
        "TOTAL PRODUCTS:",
        len(products)
    )

    print(
        "ACTIVE PRODUCTS:",
        len(active_products)
    )

    if not active_products:

        print(
            "NO ACTIVE PRODUCTS. "
            "Check PRODUCTS column G."
        )

        return

    # Prevent ambiguous SKU collisions.

    key_counts = {}

    for product in active_products:

        key = get_key(
            product["retailer"],
            product["sku"]
        )

        key_counts[key] = (
            key_counts.get(key, 0) + 1
        )

    # ======================================
    # PROCESS PRODUCTS
    # ======================================

    for product in active_products:

        retailer = str(
            product["retailer"]
        ).strip().upper()

        sku = str(
            product["sku"]
        ).strip()

        url = str(
            product["url"]
        ).strip()

        key = get_key(
            retailer,
            sku
        )

        active_keys.add(key)

        print("------------------------------")
        print("RETAILER:", retailer)
        print("SKU:", sku)
        print("URL:", url)

        if key_counts[key] > 1:

            skipped += 1

            message = "DUPLICATE_ACTIVE_SKU"

            print("SKIPPED:", message)

            log_rows.append([
                now(),
                retailer,
                url,
                message
            ])

            if key in current:

                current[key]["stock"] = "STALE"

            continue

        try:

            # Read current and original prices.

            price, stock, original_price = (
                read_price(product)
            )

            # First observed historical price.

            baseline = baselines.setdefault(
                key,
                price
            )

            # Actual historical discount.

            discount = round(
                (baseline - price)
                / baseline * 100,
                2
            )

            # Advertised discount.

            advertised_discount = ""
            saving_amount = ""

            if (
                original_price
                and original_price >= price
            ):

                saving_amount = (
                    original_price - price
                )

                advertised_discount = round(
                    saving_amount
                    / original_price * 100,
                    2
                )

            # Save all 14 columns.

            row = [
                now(),
                retailer,
                sku,
                product["model"],
                product["storage"],
                product["color"],
                price,
                baseline,
                discount,
                stock,
                url,
                original_price or "",
                advertised_discount,
                saving_amount
            ]

            current[key] = dict(
                zip(HEADERS, row)
            )

            history_rows.append(row)

            log_rows.append([
                now(),
                retailer,
                url,
                "OK"
            ])

            success += 1

            print(
                f"OK: {retailer} | "
                f"{sku} | {price:,}"
            )

            print(
                "ORIGINAL PRICE:",
                original_price
            )

            print(
                "BASELINE PRICE:",
                baseline
            )

            print(
                "ADVERTISED DISCOUNT:",
                advertised_discount
            )

            print(
                "HISTORICAL DISCOUNT:",
                discount
            )

        except Exception as error:

            failed += 1

            message = str(error)[:300]

            log_rows.append([
                now(),
                retailer,
                url,
                f"ERROR: {message}"
            ])

            # Preserve old price on failure.

            if key in current:

                current[key]["stock"] = "STALE"

            print(
                f"ERROR: {retailer} | "
                f"{sku} | {message}"
            )

        time.sleep(3)

    # ======================================
    # SAVE PRICE HISTORY
    # ======================================

    if history_rows:

        history_ws.append_rows(
            history_rows,
            value_input_option="RAW"
        )

        print(
            "HISTORY ROWS SAVED:",
            len(history_rows)
        )

    # ======================================
    # SAVE CRAWL LOG
    # ======================================

    if log_rows:

        log_ws.append_rows(
            log_rows,
            value_input_option="RAW"
        )

    # ======================================
    # UPDATE CURRENT PRICE
    # ======================================

    current_rows = [
        [
            row.get(field, "")
            for field in HEADERS
        ]
        for key, row in sorted(
            current.items()
        )
        if key in active_keys
    ]

    values = [HEADERS] + current_rows

    extra = max(
        0,
        len(old_current) - len(current_rows)
    )

    values += [
        [""] * len(HEADERS)
        for _ in range(extra)
    ]

    if len(values) > current_ws.row_count:

        current_ws.add_rows(
            len(values)
            - current_ws.row_count
        )

    current_ws.update(
        range_name="A1",
        values=values,
        value_input_option="RAW"
    )

    # ======================================
    # FINAL RESULT
    # ======================================

    print("==============================")
    print(f"SUCCESS: {success}")
    print(f"FAILED: {failed}")
    print(f"SKIPPED: {skipped}")
    print("==============================")

    if success == 0:

        raise RuntimeError(
            "No product prices collected. "
            "Check CRAWL_LOG."
        )


if __name__ == "__main__":

    main()
