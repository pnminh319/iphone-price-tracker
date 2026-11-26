import os
import re
import json
import time
import sys

from collections import Counter
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
import gspread


VERSION = "V4.2"

VN_TZ = timezone(timedelta(hours=7))

USER_AGENT = "iPhonePriceTracker/1.0"

DOMAINS = {
    "TGDD": "thegioididong.com",
    "FPT": "fptshop.com.vn",
    "CPS": "cellphones.com.vn",
    "HHM": "hoanghamobile.com",
    "DDV": "didongviet.vn",
    "VTS": "viettelstore.vn",
    "SDK": "shopdunk.com",
    "PV": "phongvu.vn", "MTM": "minhtuanmobile.com", "TZ": "topzone.vn", "H24": "24hstore.vn"
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

PRODUCT_HEADERS = [
    "sku",
    "model",
    "storage",
    "color",
    "retailer",
    "url",
    "active"
]

LOG_HEADERS = [
    "timestamp",
    "retailer",
    "url",
    "status"
]

ALIASES = {
    "current_price": "price",
    "old_price": "original_price",
    "list_price": "original_price",
    "regular_price": "original_price",
    "product_url": "url",
    "updated_at": "timestamp",
    "advertised_discount": "advertised_discount_pct",
    "saving": "saving_amount",
    "savings": "saving_amount"
}

session = requests.Session()

session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept-Language": "vi-VN,vi;q=0.9"
})

robots_cache = {}


# ==========================================
# TIME
# ==========================================

def now():
    return datetime.now(VN_TZ).isoformat(
        timespec="seconds"
    )


# ==========================================
# GOOGLE SHEETS
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
# NORMALIZE HEADER
# ==========================================

def normalize_header(value):

    value = str(value or "")

    value = value.replace("\ufeff", "")
    value = value.replace("\u200b", "")

    value = value.strip().lower()

    value = re.sub(
        r"[\s\-]+",
        "_",
        value
    )

    return ALIASES.get(value, value)


# ==========================================
# AUTOMATIC HEADER MIGRATION
# ==========================================

def ensure_headers(ws, required, optional=None):

    optional = set(optional or [])

    values = ws.get_all_values()

    if not values:

        headers = list(required)

    else:

        original = list(values[0])

        while original and not original[-1].strip():
            original.pop()

        headers = [
            normalize_header(x)
            for x in original
        ]

        print("SHEET:", ws.title)
        print("OLD HEADERS:", headers)

        if "" in headers:

            raise ValueError(
                f"EMPTY_HEADER: {ws.title}"
            )

        if len(headers) != len(set(headers)):

            raise ValueError(
                f"DUPLICATE_HEADERS: {ws.title}"
            )

        for row in values[1:]:

            if any(
                str(x).strip()
                for x in row[len(headers):]
            ):

                raise ValueError(
                    f"UNLABELED_DATA: {ws.title}"
                )

        missing = [
            h for h in required
            if h not in headers
        ]

        has_data = any(
            any(str(x).strip() for x in row)
            for row in values[1:]
        )

        missing_required = [
            h for h in missing
            if h not in optional
        ]

        if has_data and missing_required:

            raise ValueError(
                f"MISSING_COLUMNS: {ws.title}: "
                f"{missing_required}"
            )

        headers.extend(missing)

        print("ADDED COLUMNS:", missing)

    if ws.col_count < len(headers):

        ws.add_cols(
            len(headers) - ws.col_count
        )

    ws.update(
        range_name="A1",
        values=[headers],
        value_input_option="RAW"
    )

    print("HEADERS READY:", ws.title)
    print("FINAL HEADERS:", headers)

    return headers


# ==========================================
# READ SHEET RECORDS
# ==========================================

def load_records(ws):

    values = ws.get_all_values(
        value_render_option="UNFORMATTED_VALUE"
    )

    if not values:
        return []

    headers = [
        normalize_header(x)
        for x in values[0]
    ]

    records = []

    for row in values[1:]:

        if not any(
            str(x).strip()
            for x in row
        ):
            continue

        row = list(row)

        row += [""] * max(
            0,
            len(headers) - len(row)
        )

        records.append(
            dict(zip(headers, row))
        )

    return records


# ==========================================
# NORMALIZE PRICE
# ==========================================

def normalize_price(value):

    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):

        if 1000000 <= value <= 100000000:
            return int(value)

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

        if re.fullmatch(r"\d+(?:[.,]0{1,2})?", text):

            price = int(
                float(text.replace(",", "."))
            )

        elif re.fullmatch(
            r"\d{1,3}(?:\.\d{3})+",
            text
        ):

            price = int(
                text.replace(".", "")
            )

        elif re.fullmatch(
            r"\d{1,3}(?:,\d{3})+",
            text
        ):

            price = int(
                text.replace(",", "")
            )

        else:
            return None

        if 1000000 <= price <= 100000000:
            return price

    except (ValueError, TypeError):
        pass

    return None


# ==========================================
# WALK JSON
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

    model = str(
        product.get("model", "")
    ).strip().lower()

    storage = re.sub(
        r"\s+",
        "",
        str(product.get("storage", "")).lower()
    )

    if not model or not storage:

        raise ValueError(
            "MISSING_MODEL_OR_STORAGE"
        )

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

            if "Product" not in types:
                continue

            name = str(
                node.get("name", "")
            ).lower()

            match = re.search(
                r"(?<!\w)" + re.escape(model) + r"(?!\w)",
                name
            )

            if not match:
                continue

            suffix = name[match.end():]

            if re.match(
                r"\s*(pro|plus|max|mini|air)\b",
                suffix
            ):
                continue

            capacities = [
                re.sub(r"\s+", "", x)
                for x in re.findall(
                    r"\d+\s*(?:gb|tb)\b",
                    name
                )
            ]

            if storage not in capacities:
                continue

            if any(x in name for x in [
                "cũ",
                "like new",
                "refurbished",
                "đã qua sử dụng",
                "trầy xước"
            ]):
                continue

            offers = node.get("offers", [])

            if isinstance(offers, dict):
                offers = [offers]

            for offer in offers:

                if not isinstance(offer, dict):
                    continue

                offer_type = offer.get("@type", [])

                if isinstance(offer_type, str):
                    offer_type = [offer_type]

                if "Offer" not in offer_type:
                    continue

                if str(
                    offer.get("priceCurrency", "")
                ).upper() != "VND":
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
                    candidates.add((price, stock))

    if len(candidates) == 1:

        price, stock = candidates.pop()

        print("JSONLD PRICE:", price)

        return price, stock

    if len(candidates) > 1:

        raise ValueError(
            "AMBIGUOUS_PRICE"
        )

    raise ValueError("PRICE_NOT_FOUND")


# ==========================================
# VALIDATE URL
# ==========================================

def check_url(retailer, url):

    retailer = str(retailer).strip().upper()

    if retailer not in DOMAINS:
        raise ValueError("UNKNOWN_RETAILER")

    parsed = urlparse(url)

    host = (parsed.hostname or "").lower()

    domain = DOMAINS[retailer]

    if parsed.scheme != "https":
        raise ValueError("HTTPS_REQUIRED")

    if parsed.username or parsed.password:
        raise ValueError("INVALID_URL_CREDENTIALS")

    if (
        host != domain
        and not host.endswith("." + domain)
    ):
        raise ValueError("INVALID_RETAILER_URL")


# ==========================================
# ROBOTS.TXT
# ==========================================

def check_robots(url):

    parsed = urlparse(url)

    origin = (
        f"{parsed.scheme}://{parsed.netloc}"
    )

    if origin not in robots_cache:

        try:

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

        except requests.RequestException as error:

            robots_cache[origin] = error

    parser = robots_cache[origin]

    if isinstance(parser, Exception):

        raise ValueError(
            f"ROBOTS_UNAVAILABLE: {parser}"
        )

    if parser and not parser.can_fetch(
        USER_AGENT,
        url
    ):

        raise ValueError("ROBOTS_DISALLOWED")


# ==========================================
# PRODUCT KEY
# ==========================================

def get_key(retailer, sku):

    return (
        str(retailer).strip().upper(),
        str(sku).strip().upper()
    )


def active(value):

    return str(value).strip().lower() in [
        "true",
        "1",
        "1.0",
        "yes",
        "on"
    ]


# ==========================================
# SAVE RECORDS
# ==========================================

def append_records(ws, records, headers):

    if not records:
        return

    rows = [
        [record.get(h, "") for h in headers]
        for record in records
    ]

    ws.append_rows(
        rows,
        value_input_option="RAW",
        table_range="A1"
    )

    print("SAVED:", ws.title, len(rows))


# ==========================================
# MAIN
# ==========================================

def main():

    from price_reader import read_price

    print("=" * 50)
    print("IPHONE PRICE TRACKER", VERSION)
    print("START:", now())
    print("=" * 50)

    book = get_book()

    products_ws = book.worksheet("PRODUCTS")

    current_ws = book.worksheet("CURRENT_PRICE")

    history_ws = book.worksheet("PRICE_HISTORY")

    log_ws = book.worksheet("CRAWL_LOG")

    # Automatically upgrade sheet structure.

    ensure_headers(
        products_ws,
        PRODUCT_HEADERS
    )

    optional = [
        "original_price",
        "advertised_discount_pct",
        "saving_amount"
    ]

    current_headers = ensure_headers(
        current_ws,
        HEADERS,
        optional
    )

    history_headers = ensure_headers(
        history_ws,
        HEADERS,
        optional
    )

    log_headers = ensure_headers(
        log_ws,
        LOG_HEADERS,
        ["status"]
    )

    products = load_records(products_ws)

    old_current = load_records(current_ws)

    history = load_records(history_ws)

    current = {}

    baselines = {}

    # Preserve previous current prices.

    for row in old_current:

        key = get_key(
            row.get("retailer", ""),
            row.get("sku", "")
        )

        if not all(key):

            raise ValueError(
                "CURRENT_ROW_MISSING_KEY"
            )

        if key in current:

            raise ValueError(
                f"DUPLICATE_CURRENT_KEY: {key}"
            )

        current[key] = row

    # Preserve historical baseline.

    for row in history + old_current:

        key = get_key(
            row.get("retailer", ""),
            row.get("sku", "")
        )

        baseline = (
            normalize_price(
                row.get("baseline_price")
            )
            or normalize_price(
                row.get("price")
            )
        )

        if all(key) and baseline:

            baselines.setdefault(
                key,
                baseline
            )

    active_products = [
        p for p in products
        if active(p.get("active"))
        and str(
            p.get("model", "")
        ).strip().lower().startswith("iphone")
    ]

    print("TOTAL PRODUCTS:", len(products))
    print("ACTIVE PRODUCTS:", len(active_products))

    if not active_products:

        print("NO ACTIVE PRODUCTS")

        return

    key_counts = Counter(
        get_key(
            p.get("retailer", ""),
            p.get("sku", "")
        )
        for p in active_products
    )

    history_records = []

    log_records = []

    success = 0
    failed = 0
    skipped = 0

    # ======================================
    # PROCESS PRODUCTS
    # ======================================

    for index, product in enumerate(
        active_products,
        start=1
    ):

        retailer = str(
            product.get("retailer", "")
        ).strip().upper()

        sku = str(
            product.get("sku", "")
        ).strip().upper()

        url = str(
            product.get("url", "")
        ).strip()

        key = get_key(retailer, sku)

        print("-" * 50)

        print(
            f"PRODUCT {index}/{len(active_products)}"
        )

        print("RETAILER:", retailer)
        print("SKU:", sku)
        print("URL:", url)

        if not all(key) or key_counts[key] > 1:

            skipped += 1

            log_records.append({
                "timestamp": now(),
                "retailer": retailer,
                "sku": sku,
                "url": url,
                "status": "SKIPPED",
                "message": "DUPLICATE_OR_INVALID_SKU"
            })

            if key in current:
                current[key]["stock"] = "STALE"

            continue

        try:

            check_url(retailer, url)

            result = read_price(product)

            if not isinstance(result, (tuple, list)):

                raise ValueError(
                    "INVALID_READER_RESULT"
                )

            if len(result) == 3:

                price, stock, original_price = result

            elif len(result) == 2:

                price, stock = result

                original_price = None

            else:

                raise ValueError(
                    "INVALID_READER_RESULT"
                )

            price = normalize_price(price)

            original_price = normalize_price(
                original_price
            )

            if not price:

                raise ValueError(
                    "INVALID_CURRENT_PRICE"
                )

            if original_price and original_price < price:

                original_price = None

            baseline = baselines.setdefault(
                key,
                price
            )

            discount = round(
                (baseline - price)
                / baseline * 100,
                2
            )

            advertised_discount = ""

            saving_amount = ""

            if original_price:

                saving_amount = (
                    original_price - price
                )

                advertised_discount = round(
                    saving_amount
                    / original_price * 100,
                    2
                )

            record = {
                "timestamp": now(),
                "retailer": retailer,
                "sku": sku,
                "model": product.get("model", ""),
                "storage": product.get("storage", ""),
                "color": product.get("color", "UNKNOWN"),
                "price": price,
                "baseline_price": baseline,
                "discount_pct": discount,
                "stock": str(stock or "UNKNOWN"),
                "url": url,
                "original_price": original_price or "",
                "advertised_discount_pct": advertised_discount,
                "saving_amount": saving_amount
            }

            current[key] = {
                **current.get(key, {}),
                **record
            }

            history_records.append(record)

            log_records.append({
                "timestamp": now(),
                "retailer": retailer,
                "sku": sku,
                "url": url,
                "status": "OK",
                "message": "PRICE_UPDATED"
            })

            success += 1

            print("CURRENT PRICE:", price)
            print("ORIGINAL PRICE:", original_price)
            print("BASELINE PRICE:", baseline)
            print("DISCOUNT:", discount)
            print("ADVERTISED DISCOUNT:", advertised_discount)
            print("SAVING:", saving_amount)

            print(
                f"OK: {retailer} | {sku} | {price:,}"
            )

        except Exception as error:

            failed += 1

            message = str(error)[:500]

            print(
                f"ERROR: {retailer} | {sku} | {message}"
            )

            log_records.append({
                "timestamp": now(),
                "retailer": retailer,
                "sku": sku,
                "url": url,
                "status": f"ERROR: {message}",
                "message": message
            })

            if key in current:

                current[key]["stock"] = "STALE"

        time.sleep(3)

    # ======================================
    # SAVE HISTORY
    # ======================================

    append_records(
        history_ws,
        history_records,
        history_headers
    )

    # ======================================
    # SAVE LOG
    # ======================================

    append_records(
        log_ws,
        log_records,
        log_headers
    )

    # ======================================
    # UPDATE CURRENT PRICE
    # ======================================

    for key in current:

        if key not in key_counts:

            current[key]["stock"] = "INACTIVE"

    current_rows = [
        [
            record.get(h, "")
            for h in current_headers
        ]
        for key, record in sorted(
            current.items()
        )
    ]

    values = [
        current_headers
    ] + current_rows

    if len(values) > current_ws.row_count:

        current_ws.add_rows(
            len(values) - current_ws.row_count
        )

    current_ws.update(
        range_name="A1",
        values=values,
        value_input_option="RAW"
    )

    print(
        "CURRENT PRICE SAVED:",
        len(current_rows)
    )

    # ======================================
    # FINAL RESULT
    # ======================================

    print("=" * 50)
    print("VERSION:", VERSION)
    print("SUCCESS:", success)
    print("FAILED:", failed)
    print("SKIPPED:", skipped)
    print("FINISHED:", now())
    print("=" * 50)

    if success == 0:

        raise RuntimeError(
            "No prices collected. "
            "Previous prices preserved. "
            "Check CRAWL_LOG."
        )


if __name__ == "__main__":

    sys.modules["main"] = sys.modules[__name__]

    main()
