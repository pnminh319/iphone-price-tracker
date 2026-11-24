
import argparse
import json
import sys

from pathlib import Path

import gspread

from main import (
    get_book,
    HEADERS,
    now,
    normalize_price,
    load_records,
    append_records
)

from local_discover import (
    SOURCES,
    fetch_html,
    extract_products,
    BLOCKED
)

from retailers_v52 import parse_html


VERSION = "V5.3"

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "local" / "test_data"

CURRENT_SHEET = "LOCAL_CURRENT_PRICE"
HISTORY_SHEET = "LOCAL_PRICE_HISTORY"
LOG_SHEET = "LOCAL_CRAWL_LOG"

LOCAL_HEADERS = HEADERS + ["scope"]

LOG_HEADERS = [
    "timestamp",
    "retailer",
    "url",
    "status",
    "message"
]

sys.stdout.reconfigure(
    encoding="utf-8",
    errors="replace"
)

sys.stderr.reconfigure(
    encoding="utf-8",
    errors="replace"
)


def get_sheet(book, name, headers):

    try:
        ws = book.worksheet(name)

    except gspread.WorksheetNotFound:

        ws = book.add_worksheet(
            title=name,
            rows=1000,
            cols=len(headers)
        )

    existing = ws.row_values(1)

    if not existing:

        ws.update(
            range_name="A1",
            values=[headers],
            value_input_option="RAW"
        )

    elif existing != headers:

        raise ValueError(
            f"INVALID_HEADERS: {name}"
        )

    return ws


def get_key(row):

    return (
        str(row.get("retailer", ""))
        .strip().upper(),
        str(row.get("sku", ""))
        .strip().upper()
    )


def make_log(retailer, url, status, message):

    return {
        "timestamp": now(),
        "retailer": retailer,
        "url": url,
        "status": status,
        "message": str(message)[:500]
    }


def discover_all(limit=0):

    products = []
    logs = []

    for retailer, source in SOURCES.items():

        try:

            html = fetch_html(
                retailer,
                source
            )

            rows = extract_products(
                html,
                retailer,
                source
            )

            if not rows:
                raise ValueError(
                    "NO_PRODUCTS_FOUND"
                )

            if limit:
                rows = rows[:limit]

            products.extend(rows)

            logs.append(
                make_log(
                    retailer,
                    source,
                    "OK",
                    f"DISCOVERED: {len(rows)}"
                )
            )

            print(
                retailer,
                "DISCOVERED:",
                len(rows)
            )

        except Exception as error:

            logs.append(
                make_log(
                    retailer,
                    source,
                    "ERROR",
                    error
                )
            )

            print(
                retailer,
                "DISCOVERY ERROR:",
                error
            )

    return products, logs


def crawl_products(products):

    collected = {}
    conflicts = set()
    logs = []

    for index, product in enumerate(
        products,
        start=1
    ):

        retailer = product["retailer"]
        url = product["url"]

        if retailer in BLOCKED:
            continue

        print("-" * 60)

        print(
            f"PRODUCT {index}/{len(products)}"
        )

        print(retailer, url)

        try:

            html = fetch_html(
                retailer,
                url
            )

            rows = parse_html(
                retailer,
                html,
                page_url=url
            )

            accepted = 0

            for row in rows:

                if (
                    row["model"].lower()
                    != product["model"].lower()
                ):
                    continue

                expected_storage = (
                    product.get("storage")
                )

                if (
                    expected_storage
                    and row["storage"]
                    != expected_storage
                ):
                    continue

                key = get_key(row)

                if not all(key):
                    continue

                if key in conflicts:
                    continue

                if key in collected:

                    previous = collected[key]

                    if (
                        previous["price"]
                        != row["price"]
                        or previous["stock"]
                        != row["stock"]
                    ):

                        conflicts.add(key)
                        collected.pop(key, None)

                        logs.append(
                            make_log(
                                retailer,
                                url,
                                "CONFLICT",
                                row["sku"]
                            )
                        )

                        continue

                    if (
                        previous.get("original_price")
                        and not row.get("original_price")
                    ):
                        continue

                collected[key] = row

                accepted += 1

                print(
                    "OFFER:",
                    row["sku"],
                    row["price"],
                    row["stock"]
                )

            if not accepted:
                raise ValueError(
                    "NO_VALID_OFFERS"
                )

            logs.append(
                make_log(
                    retailer,
                    url,
                    "OK",
                    f"OFFERS: {accepted}"
                )
            )

        except Exception as error:

            logs.append(
                make_log(
                    retailer,
                    url,
                    "ERROR",
                    error
                )
            )

            print("ERROR:", error)

    return list(collected.values()), logs


def build_record(row, baselines):

    key = get_key(row)

    price = normalize_price(
        row.get("price")
    )

    original = normalize_price(
        row.get("original_price")
    )

    baseline = baselines.get(key)

    if price and not baseline:

        baseline = price
        baselines[key] = price

    discount = ""

    if price and baseline:

        discount = round(
            (baseline - price)
            / baseline * 100,
            2
        )

    advertised = ""
    saving = ""

    if price and original and original > price:

        saving = original - price

        advertised = round(
            saving / original * 100,
            2
        )

    else:
        original = None

    return {
        "timestamp": now(),
        "retailer": row["retailer"],
        "sku": row["sku"],
        "model": row["model"],
        "storage": row["storage"],
        "color": row["color"],
        "price": price or "",
        "baseline_price": baseline or "",
        "discount_pct": discount,
        "stock": row["stock"],
        "url": row["url"],
        "original_price": original or "",
        "advertised_discount_pct": advertised,
        "saving_amount": saving,
        "scope": row.get(
            "scope",
            "PRODUCT"
        )
    }


def save_to_sheets(rows, logs):

    book = get_book()

    current_ws = get_sheet(
        book,
        CURRENT_SHEET,
        LOCAL_HEADERS
    )

    history_ws = get_sheet(
        book,
        HISTORY_SHEET,
        LOCAL_HEADERS
    )

    log_ws = get_sheet(
        book,
        LOG_SHEET,
        LOG_HEADERS
    )

    old_current = load_records(
        current_ws
    )

    old_history = load_records(
        history_ws
    )

    current = {}
    baselines = {}

    for row in old_current:

        key = get_key(row)

        if not all(key):
            raise ValueError(
                "INVALID_CURRENT_KEY"
            )

        if key in current:
            raise ValueError(
                f"DUPLICATE_CURRENT_KEY: {key}"
            )

        current[key] = row

    for row in old_history + old_current:

        key = get_key(row)

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

    new_history = []
    updated = set()

    for row in rows:

        record = build_record(
            row,
            baselines
        )

        key = get_key(record)

        current[key] = record
        updated.add(key)

        new_history.append(record)

    # Preserve previous observations when
    # a retailer or product cannot be read.

    for key, row in current.items():

        if key not in updated:
            row["stock"] = "STALE"

    append_records(
        history_ws,
        new_history,
        LOCAL_HEADERS
    )

    append_records(
        log_ws,
        logs,
        LOG_HEADERS
    )

    values = [LOCAL_HEADERS]

    for key, row in sorted(
        current.items()
    ):

        values.append([
            row.get(header, "")
            for header in LOCAL_HEADERS
        ])

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

    print(
        "LOCAL CURRENT:",
        len(current)
    )

    print(
        "LOCAL HISTORY ADDED:",
        len(new_history)
    )


def save_preview(rows, logs):

    OUTPUT.mkdir(
        parents=True,
        exist_ok=True
    )

    payload = {
        "version": VERSION,
        "timestamp": now(),
        "offers": rows,
        "logs": logs
    }

    path = OUTPUT / "local_prices.json"
    temp = path.with_suffix(".tmp")

    temp.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    temp.replace(path)

    print("PREVIEW SAVED:", path)


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--write",
        action="store_true",
        help="Write results to Google Sheets"
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum URLs per retailer"
    )

    args = parser.parse_args()

    if args.limit < 0:
        parser.error(
            "--limit must be non-negative"
        )

    print("=" * 60)
    print("LOCAL CRAWLER", VERSION)
    print("START:", now())

    print(
        "MODE:",
        "GOOGLE SHEETS"
        if args.write
        else "PREVIEW"
    )

    products, discovery_logs = discover_all(
        args.limit
    )

    rows, crawl_logs = crawl_products(
        products
    )

    logs = discovery_logs + crawl_logs

    save_preview(
        rows,
        logs
    )

    valid = [
        row for row in rows
        if normalize_price(row.get("price"))
    ]

    if not valid:

        print("NO VALID PRICES")

        return 1

    if args.write:

        if args.limit:

            raise ValueError(
                "Do not use --write with --limit"
            )

        save_to_sheets(
            rows,
            logs
        )

    print("=" * 60)
    print("TOTAL OFFERS:", len(rows))
    print("VALID PRICES:", len(valid))
    print("FINISHED:", now())
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
