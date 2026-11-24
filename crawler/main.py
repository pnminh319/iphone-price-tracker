
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


VN_TZ = timezone(timedelta(hours=7))

DOMAINS = {
    "TGDD": "thegioididong.com",
    "FPT": "fptshop.com.vn",
    "CPS": "cellphones.com.vn",
    "HHM": "hoanghamobile.com",
    "DDV": "didongviet.vn",
    "VTS": "viettelstore.vn",
}

HEADERS = [
    "timestamp", "retailer", "sku",
    "model", "storage", "color",
    "price", "baseline_price",
    "discount_pct", "stock", "url"
]

LOG_HEADERS = [
    "timestamp", "retailer", "url", "status"
]

USER_AGENT = "iPhonePriceTracker/1.0"

session = requests.Session()
session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept-Language": "vi-VN,vi;q=0.9"
})

robots_cache = {}


def now():
    return datetime.now(VN_TZ).isoformat(
        timespec="seconds"
    )


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


def normalize_price(value):
    if value is None:
        return None

    value = str(value).strip()

    try:
        if re.fullmatch(r"\d+(\.\d{1,2})?", value):
            price = int(float(value))
        else:
            price = int(re.sub(r"\D", "", value))

        if 1000000 <= price <= 100000000:
            return price

    except (ValueError, TypeError):
        pass

    return None


def walk_json(data):
    if isinstance(data, dict):
        yield data

        for value in data.values():
            yield from walk_json(value)

    elif isinstance(data, list):
        for item in data:
            yield from walk_json(item)


def get_jsonld_price(soup, product):
    candidates = set()

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

            model = str(product["model"]).lower()

            storage = str(
                product["storage"]
            ).lower().replace(" ", "")

            compact_name = name.replace(" ", "")

            if model not in name:
                continue

            # Avoid matching Pro/Plus models
            # to a standard model.
            suffix = name.split(model, 1)[1]

            if re.match(
                r"\s*(pro|plus|max|air|mini)",
                suffix
            ):
                continue

            if storage not in compact_name:
                continue

            if any(x in name for x in [
                "cũ", "like new", "đã qua sử dụng"
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
                    offer.get("availability", "UNKNOWN")
                ).split("/")[-1]

                if price:
                    candidates.add((price, stock))

    if len(candidates) == 1:
        return candidates.pop()

    if len(candidates) > 1:
        raise ValueError("AMBIGUOUS_PRICE")

    raise ValueError("PRICE_NOT_FOUND")


def check_url(retailer, url):
    domain = DOMAINS[retailer]
    parsed = urlparse(url)

    host = (parsed.hostname or "").lower()

    if parsed.scheme != "https":
        raise ValueError("HTTPS_REQUIRED")

    if host != domain and not host.endswith(
        "." + domain
    ):
        raise ValueError("INVALID_RETAILER_URL")


def check_robots(url):
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

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
            parser.parse(response.text.splitlines())
            robots_cache[origin] = parser

    parser = robots_cache[origin]

    if parser and not parser.can_fetch(
        USER_AGENT, url
    ):
        raise ValueError("ROBOTS_DISALLOWED")


def crawl_product(product):
    retailer = str(
        product["retailer"]
    ).strip().upper()

    url = str(product["url"]).strip()

    check_url(retailer, url)
    check_robots(url)

    response = session.get(
        url,
        timeout=30,
        allow_redirects=False
    )

    if 300 <= response.status_code < 400:
        raise ValueError("CHECK_REDIRECT_URL")

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    return get_jsonld_price(soup, product)


def get_key(retailer, sku):
    return (
        str(retailer).strip().upper(),
        str(sku).strip()
    )


def active(value):
    return str(value).strip().lower() in [
        "true", "1", "yes"
    ]


def main():
    book = get_book()

    products_ws = book.worksheet("PRODUCTS")
    current_ws = book.worksheet("CURRENT_PRICE")
    history_ws = book.worksheet("PRICE_HISTORY")
    log_ws = book.worksheet("CRAWL_LOG")

    for ws, headers in [
        (current_ws, HEADERS),
        (history_ws, HEADERS),
        (log_ws, LOG_HEADERS)
    ]:
        if not ws.row_values(1):
            ws.append_row(headers)

        elif ws.row_values(1) != headers:
            raise ValueError(
                f"INVALID_HEADERS: {ws.title}"
            )

    products = products_ws.get_all_records()
    old_current = current_ws.get_all_records()

    current = {
        get_key(row["retailer"], row["sku"]): row
        for row in old_current
    }

    # Use the first verified observed price as
    # the initial comparison baseline.
    history = history_ws.get_all_records()
    baselines = {}

    for row in history:
        key = get_key(row["retailer"], row["sku"])

        price = normalize_price(
            row["baseline_price"]
        ) or normalize_price(row["price"])

        if price:
            baselines.setdefault(key, price)

    history_rows = []
    log_rows = []
    active_keys = set()

    success = 0
    failed = 0

    for product in products:
        if not active(product.get("active")):
            continue

        if not str(
            product.get("model", "")
        ).lower().startswith("iphone"):
            continue

        retailer = str(
            product["retailer"]
        ).strip().upper()

        sku = str(product["sku"]).strip()
        url = str(product["url"]).strip()

        key = get_key(retailer, sku)
        active_keys.add(key)

        try:
            from price_reader import read_price
            price, stock = read_price(product)

            baseline = baselines.setdefault(
                key, price
            )

            discount = round(
                (baseline - price) / baseline * 100,
                2
            )

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
                url
            ]

            current[key] = dict(zip(HEADERS, row))
            history_rows.append(row)

            log_rows.append([
                now(), retailer, url, "OK"
            ])

            success += 1

            print(
                f"OK: {retailer} | {sku} | {price:,}"
            )

        except Exception as error:
            failed += 1

            message = str(error)[:300]

            log_rows.append([
                now(), retailer, url,
                f"ERROR: {message}"
            ])

            # Preserve the last known price
            # but flag it as stale.
            if key in current:
                current[key]["stock"] = "STALE"

            print(
                f"ERROR: {retailer} | {sku} | {message}"
            )

        time.sleep(3)

    if history_rows:
        history_ws.append_rows(
            history_rows,
            value_input_option="RAW"
        )

    if log_rows:
        log_ws.append_rows(
            log_rows,
            value_input_option="RAW"
        )

    current_rows = [
        [row.get(field, "") for field in HEADERS]
        for key, row in sorted(current.items())
        if key in active_keys
    ]

    values = [HEADERS] + current_rows

    # Replace old rows without clearing
    # the complete worksheet first.
    extra = max(
        0, len(old_current) - len(current_rows)
    )

    values += [[""] * len(HEADERS) for _ in range(extra)]

    if len(values) > current_ws.row_count:
        current_ws.add_rows(
            len(values) - current_ws.row_count
        )

    current_ws.update(
        range_name="A1",
        values=values,
        value_input_option="RAW"
    )

    print("========================")
    print(f"SUCCESS: {success}")
    print(f"FAILED: {failed}")
    print("========================")

    if success == 0:
        raise RuntimeError(
            "No product prices collected. Check CRAWL_LOG."
        )


if __name__ == "__main__":
    main()
