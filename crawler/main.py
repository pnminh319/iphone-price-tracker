
import os
import json
import time
import re

from collections import Counter
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
import gspread

from bs4 import BeautifulSoup


# ==========================================
# 1. CONFIGURATION
# ==========================================

VERSION = "V4.1"

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

HEADER_ALIASES = {
    "current_price": "price",
    "old_price": "original_price",
    "list_price": "original_price",
    "regular_price": "original_price",
    "originalprice": "original_price",
    "advertised_discount": "advertised_discount_pct",
    "saving": "saving_amount",
    "savings": "saving_amount",
    "product_url": "url",
    "updated_at": "timestamp"
}

session = requests.Session()

session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept-Language": "vi-VN,vi;q=0.9"
})

robots_cache = {}


# ==========================================
# 2. TIME
# ==========================================

def now():

    return datetime.now(VN_TZ).isoformat(
        timespec="seconds"
    )


# ==========================================
# 3. GOOGLE SHEETS CONNECTION
# ==========================================

def get_book():

    raw_credentials = os.environ.get(
        "GOOGLE_CREDENTIALS", ""
    ).strip()

    spreadsheet_id = os.environ.get(
        "SPREADSHEET_ID", ""
    ).strip()

    if not raw_credentials:

        raise ValueError(
            "MISSING_GOOGLE_CREDENTIALS"
        )

    if not spreadsheet_id:

        raise ValueError(
            "MISSING_SPREADSHEET_ID"
        )

    credentials = json.loads(
        raw_credentials
    )

    client = gspread.service_account_from_dict(
        credentials,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets"
        ]
    )

    return client.open_by_key(
        spreadsheet_id
    )


# ==========================================
# 4. NORMALIZE HEADERS
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

    return HEADER_ALIASES.get(
        value,
        value
    )


# ==========================================
# 5. AUTO FIX SHEET HEADERS
# ==========================================

def ensure_headers(
    ws,
    required_headers,
    optional_headers=None
):

    optional_headers = set(
        optional_headers or []
    )

    values = ws.get_all_values()

    if not values:

        if ws.col_count < len(required_headers):

            ws.add_cols(
                len(required_headers)
                - ws.col_count
            )

        ws.update(
            range_name="A1",
            values=[required_headers],
            value_input_option="RAW"
        )

        print(
            "HEADERS CREATED:",
            ws.title
        )

        return list(required_headers)

    original_headers = list(values[0])

    while (
        original_headers
        and not str(original_headers[-1]).strip()
    ):
        original_headers.pop()

    existing_headers = [
        normalize_header(header)
        for header in original_headers
    ]

    print("SHEET:", ws.title)
    print("EXISTING HEADERS:", existing_headers)

    counts = Counter(
        header
        for header in existing_headers
        if header
    )

    duplicates = [
        name
        for name, count in counts.items()
        if count > 1
    ]

    if duplicates:

        raise ValueError(
            f"DUPLICATE_HEADERS: "
            f"{ws.title}: {duplicates}"
        )

    if "" in existing_headers:

        raise ValueError(
            f"EMPTY_HEADER_INSIDE_TABLE: "
            f"{ws.title}"
        )

    missing = [
        header
        for header in required_headers
        if header not in existing_headers
    ]

    missing_required = [
        header
        for header in missing
        if header not in optional_headers
    ]

    if missing_required and len(values) > 1:

        raise ValueError(
            f"MISSING_REQUIRED_HEADERS: "
            f"{ws.title}: {missing_required}"
        )

    final_headers = (
        existing_headers + missing
    )

    # Prevent assigning headers to columns
    # that already contain unidentified data.

    for row in values[1:]:

        extra = row[len(existing_headers):]

        if any(str(value).strip() for value in extra):

            raise ValueError(
                f"UNLABELED_DATA_COLUMNS: "
                f"{ws.title}"
            )

    if ws.col_count < len(final_headers):

        ws.add_cols(
            len(final_headers)
            - ws.col_count
        )

    if original_headers != final_headers:

        ws.update(
            range_name="A1",
            values=[final_headers],
            value_input_option="RAW"
        )

        print(
            "HEADERS AUTO FIXED:",
            ws.title
        )

        print(
            "ADDED COLUMNS:",
            missing
        )

    else:

        print(
            "HEADERS OK:",
            ws.title
        )

    return final_headers


# ==========================================
# 6. LOAD SHEET RECORDS
# ==========================================

def load_records(ws):

    values = ws.get_all_values(
        value_render_option="UNFORMATTED_VALUE"
    )

    if not values:

        return []

    headers = [
        normalize_header(header)
        for header in values[0]
    ]

    records = []

    for row in values[1:]:

        if not any(
            str(value).strip()
            for value in row
        ):
            continue

        padded = list(row) + [""] * max(
            0,
            len(headers) - len(row)
        )

        record = dict(
            zip(headers, padded)
        )

        records.append(record)

    return records


# ==========================================
# 7. NORMALIZE PRICE
# ==========================================

def normalize_price(value):

    if value is None or isinstance(value, bool):

        return None

    if isinstance(value, (int, float)):

        if 1000000 <= value <= 100000000:

            return int(value)

        return None

    value = str(value).strip()

    value = re.sub(
        r"(VND|VNĐ|₫|đ)",
        "",
        value,
        flags=re.I
    )

    value = re.sub(r"\s+", "", value)

    try:

        if re.fullmatch(
            r"\d+(?:[.,]0{1,2})?",
            value
        ):

            price = int(
                float(value.replace(",", "."))
            )

        elif re.fullmatch(
            r"\d{1,3}(?:\.\d{3})+",
            value
        ):

            price = int(
                value.replace(".", "")
            )

        elif re.fullmatch(
            r"\d{1,3}(?:,\d{3})+",
            value
        ):

            price = int(
                value.replace(",", "")
            )

        else:

            return None

        if 1000000 <= price <= 100000000:

            return price

    except (ValueError, TypeError):

        pass

    return None


# ==========================================
# 8. WALK JSON
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
# 9. READ JSON-LD PRODUCT PRICE
# ==========================================

def get_jsonld_price(soup, product):

    candidates = set()

    expected_model = str(
        product.get("model", "")
    ).strip().lower()

    expected_storage = re.sub(
        r"\s+",
        "",
        str(product.get("storage", "")).lower()
    )

    if not expected_model or not expected_storage:

        raise ValueError(
            "MISSING_MODEL_OR_STORAGE"
        )

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

            compact_name = re.sub(
                r"\s+",
                "",
                name
            )

            model_pattern = (
                r"(?<!\w)"
                + re.escape(expected_model)
                + r"(?!\w)"
            )

            match = re.search(
                model_pattern,
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

            storage_pattern = (
                r"(?<!\d)"
                + re.escape(expected_storage)
                + r"(?!\w)"
            )

            storage_candidates = [
                re.sub(r"\s+", "", x)
                for x in re.findall(
                    r"\d+\s*(?:gb|tb)\b",
                    name
                )
            ]

            if expected_storage not in storage_candidates:

                continue

            if any(word in name for word in [
                "máy cũ",
                "hàng cũ",
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

                if any(word in condition for word in [
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

        price, stock = candidates.pop()

        print("JSONLD PRICE:", price)

        return price, stock

    if len(candidates) > 1:

        raise ValueError(
            "AMBIGUOUS_PRICE"
        )

    raise ValueError(
        "PRICE_NOT_FOUND"
    )


# ==========================================
# 10. VALIDATE RETAILER URL
# ==========================================

def check_url(retailer, url):

    retailer = str(retailer).strip().upper()

    if retailer not in DOMAINS:

        raise ValueError(
            "UNKNOWN_RETAILER"
        )

    parsed = urlparse(url)

    host = (
        parsed.hostname or ""
    ).lower()

    domain = DOMAINS[retailer]

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

    if parsed.username or parsed.password:

        raise ValueError(
            "INVALID_URL_CREDENTIALS"
        )


# ==========================================
# 11. ROBOTS.TXT
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

        raise ValueError(
            "ROBOTS_DISALLOWED"
        )


# ==========================================
# 12. PRODUCT KEY
# ==========================================

def get_key(retailer, sku):

    return (
        str(retailer).strip().upper(),
        str(sku).strip().upper()
    )


# ==========================================
# 13. ACTIVE STATUS
# ==========================================

def active(value):

    return str(value).strip().lower() in [
        "true",
        "1",
        "1.0",
        "yes",
        "on"
    ]


# ==========================================
# 14. CALCULATE DISCOUNTS
# ==========================================

def calculate_discounts(
    price,
    original_price,
    baseline
):

    historical_discount = ""

    advertised_discount = ""

    saving_amount = ""

    if baseline and baseline > 0:

        historical_discount = round(
            (baseline - price)
            / baseline * 100,
            2
        )

    if original_price and original_price >= price:

        saving_amount = (
            original_price - price
        )

        advertised_discount = round(
            saving_amount
            / original_price * 100,
            2
        )

    return (
        historical_discount,
        advertised_discount,
        saving_amount
    )


# ==========================================
# 15. CONVERT RECORD TO SHEET ROW
# ==========================================

def record_to_row(record, headers):

    return [
        record.get(header, "")
        for header in headers
    ]


# ==========================================
# 16. SAVE HISTORY
# ==========================================

def append_records(ws, records, headers):

    if not records:

        return

    rows = [
        record_to_row(record, headers)
        for record in records
    ]

    ws.append_rows(
        rows,
        value_input_option="RAW",
        table_range="A1"
    )

    print(
        f"SAVED {len(rows)} ROWS:",
        ws.title
    )


# ==========================================
# 17. SAVE CURRENT PRICE
# ==========================================

def save_current_price(
    ws,
    current,
    headers,
    old_row_count
):

    rows = [
        record_to_row(record, headers)
        for key, record in sorted(
            current.items()
        )
    ]

    values = [headers] + rows

    extra_rows = max(
        0,
        old_row_count - len(rows)
    )

    values.extend([
        [""] * len(headers)
        for _ in range(extra_rows)
    ])

    if len(values) > ws.row_count:

        ws.add_rows(
            len(values) - ws.row_count
        )

    ws.update(
        range_name="A1",
        values=values,
        value_input_option="RAW"
    )

    print(
        "CURRENT PRICE SAVED:",
        len(rows)
    )


# ==========================================
# 18. MAIN CRAWLER
# ==========================================

def main():

    from price_reader import read_price

    print("=" * 50)
    print(f"IPHONE PRICE TRACKER {VERSION}")
    print("START:", now())
    print("=" * 50)

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

    # ======================================
    # AUTO FIX HEADERS
    # ======================================

    print("CHECKING SHEET STRUCTURE...")

    ensure_headers(
        products_ws,
        PRODUCT_HEADERS
    )

    optional_price_headers = [
        "original_price",
        "advertised_discount_pct",
        "saving_amount"
    ]

    current_headers = ensure_headers(
        current_ws,
        HEADERS,
        optional_price_headers
    )

    history_headers = ensure_headers(
        history_ws,
        HEADERS,
        optional_price_headers
    )

    log_headers = ensure_headers(
        log_ws,
        LOG_HEADERS,
        optional_headers=["status"]
    )

    # ======================================
    # LOAD EXISTING DATA
    # ======================================

    products = load_records(
        products_ws
    )

    old_current = load_records(
        current_ws
    )

    history = load_records(
        history_ws
    )

    current = {}

    for row in old_current:

        key = get_key(
            row.get("retailer", ""),
            row.get("sku", "")
        )

        if all(key):

            current[key] = row

    # ======================================
    # BUILD BASELINE PRICES
    # ======================================

    baselines = {}

    for row in history:

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

    for key, row in current.items():

        baseline = (
            normalize_price(
                row.get("baseline_price")
            )
            or normalize_price(
                row.get("price")
            )
        )

        if baseline:

            baselines.setdefault(
                key,
                baseline
            )

    # ======================================
    # SELECT ACTIVE PRODUCTS
    # ======================================

    active_products = [
        product
        for product in products
        if active(
            product.get("active")
        )
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
            "Check PRODUCTS column active."
        )

        return

    key_counts = Counter(
        get_key(
            product.get("retailer", ""),
            product.get("sku", "")
        )
        for product in active_products
    )

    history_records = []
    log_records = []

    success = 0
    failed = 0
    skipped = 0

    # ======================================
    # PROCESS EACH PRODUCT
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

        model = str(
            product.get("model", "")
        ).strip()

        storage = str(
            product.get("storage", "")
        ).strip()

        color = str(
            product.get("color", "")
        ).strip() or "UNKNOWN"

        url = str(
            product.get("url", "")
        ).strip()

        key = get_key(
            retailer,
            sku
        )

        print("-" * 50)

        print(
            f"PRODUCT {index}/{len(active_products)}"
        )

        print("RETAILER:", retailer)
        print("SKU:", sku)
        print("MODEL:", model)
        print("STORAGE:", storage)
        print("COLOR:", color)
        print("URL:", url)

        # Prevent duplicate active SKUs

        if (
            not all(key)
            or key_counts[key] > 1
        ):

            skipped += 1

            message = (
                "DUPLICATE_OR_INVALID_SKU"
            )

            print("SKIPPED:", message)

            log_records.append({
                "timestamp": now(),
                "retailer": retailer,
                "sku": sku,
                "url": url,
                "status": "SKIPPED",
                "message": message
            })

            if key in current:

                current[key]["stock"] = "STALE"

            continue

        try:

            check_url(
                retailer,
                url
            )

            # ==================================
            # READ CURRENT AND ORIGINAL PRICE
            # ==================================

            result = read_price(
                product
            )

            # Compatible with both V3 and V4
            # price_reader.py return values.

            if not isinstance(result, (tuple, list)):

                raise ValueError(
                    "INVALID_PRICE_READER_RESULT"
                )

            if len(result) == 3:

                price, stock, original_price = result

            elif len(result) == 2:

                price, stock = result

                original_price = None

            else:

                raise ValueError(
                    "INVALID_PRICE_READER_RESULT"
                )

            price = normalize_price(
                price
            )

            original_price = normalize_price(
                original_price
            )

            if not price:

                raise ValueError(
                    "INVALID_CURRENT_PRICE"
                )

            if (
                original_price
                and original_price < price
            ):

                original_price = None

            # ==================================
            # PRESERVE BASELINE
            # ==================================

            baseline = baselines.setdefault(
                key,
                price
            )

            (
                historical_discount,
                advertised_discount,
                saving_amount
            ) = calculate_discounts(
                price,
                original_price,
                baseline
            )

            # ==================================
            # CREATE PRICE RECORD
            # ==================================

            record = {
                "timestamp": now(),
                "retailer": retailer,
                "sku": sku,
                "model": model,
                "storage": storage,
                "color": color,
                "price": price,
                "baseline_price": baseline,
                "discount_pct": historical_discount,
                "stock": str(stock or "UNKNOWN"),
                "url": url,
                "original_price": original_price or "",
                "advertised_discount_pct": advertised_discount,
                "saving_amount": saving_amount
            }

            # Preserve additional current fields.

            current[key] = {
                **current.get(key, {}),
                **record
            }

            history_records.append(
                record
            )

            log_records.append({
                "timestamp": now(),
                "retailer": retailer,
                "sku": sku,
                "url": url,
                "status": "OK",
                "message": "PRICE_UPDATED"
            })

            success += 1

            print(
                f"OK: {retailer} | "
                f"{sku} | {price:,}"
            )

            print(
                "CURRENT PRICE:",
                price
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
                historical_discount
            )

            print(
                "SAVING AMOUNT:",
                saving_amount
            )

        except Exception as error:

            failed += 1

            message = str(error)[:500]

            print(
                f"ERROR: {retailer} | "
                f"{sku} | {message}"
            )

            log_records.append({
                "timestamp": now(),
                "retailer": retailer,
                "sku": sku,
                "url": url,
                "status": f"ERROR: {message}",
                "message": message
            })

            # Keep last known price and timestamp.

            if key in current:

                current[key]["stock"] = "STALE"

        time.sleep(3)

    # ======================================
    # SAVE GOOGLE SHEETS
    # ======================================

    print("=" * 50)
    print("SAVING GOOGLE SHEETS")

    append_records(
        history_ws,
        history_records,
        history_headers
    )

    append_records(
        log_ws,
        log_records,
        log_headers
    )

    # Mark inactive products without deleting
    # their last recorded prices.

    active_keys = set(key_counts)

    for key in current:

        if key not in active_keys:

            current[key]["stock"] = "INACTIVE"

    save_current_price(
        current_ws,
        current,
        current_headers,
        len(old_current)
    )

    # ======================================
    # FORMAT PRICE COLUMNS
    # ======================================

    for ws, headers in [
        (current_ws, current_headers),
        (history_ws, history_headers)
    ]:

        try:

            formats = []

            for field in [
                "price",
                "baseline_price",
                "original_price",
                "saving_amount"
            ]:

                if field in headers:

                    col_index = (
                        headers.index(field) + 1
                    )

                    column = gspread.utils.rowcol_to_a1(
                        1,
                        col_index
                    )[:-1]

                    formats.append({
                        "range": f"{column}2:{column}",
                        "format": {
                            "numberFormat": {
                                "type": "NUMBER",
                                "pattern": '#,##0" đ"'
                            }
                        }
                    })

            for field in [
                "discount_pct",
                "advertised_discount_pct"
            ]:

                if field in headers:

                    col_index = (
                        headers.index(field) + 1
                    )

                    column = gspread.utils.rowcol_to_a1(
                        1,
                        col_index
                    )[:-1]

                    formats.append({
                        "range": f"{column}2:{column}",
                        "format": {
                            "numberFormat": {
                                "type": "NUMBER",
                                "pattern": '0.00"%"'
                            }
                        }
                    })

            if formats:

                ws.batch_format(
                    formats
                )

        except Exception as error:

            print(
                "FORMAT WARNING:",
                str(error)
            )

    # ======================================
    # FINAL RESULT
    # ======================================

    print("=" * 50)

    print(
        f"IPHONE PRICE TRACKER {VERSION}"
    )

    print("ACTIVE:", len(active_products))
    print("SUCCESS:", success)
    print("FAILED:", failed)
    print("SKIPPED:", skipped)
    print("FINISHED:", now())

    print("=" * 50)

    if success == 0:

        raise RuntimeError(
            "No product prices collected. "
            "Previous prices preserved. "
            "Check CRAWL_LOG."
        )


if __name__ == "__main__":

    main()
