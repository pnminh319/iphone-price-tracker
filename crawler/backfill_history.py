
import argparse
import csv
import json
import os
import re

from pathlib import Path
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup

VERSION = "V5.4"
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "local" / "test_data"

SHEET_NAME = "BACKFILL_PRICE_HISTORY"
TZ = timezone(timedelta(hours=7))

HEADERS = [
    "timestamp",
    "retailer",
    "sku",
    "model",
    "storage",
    "color",
    "price",
    "original_price",
    "advertised_discount_pct",
    "saving_amount",
    "stock",
    "scope",
    "source",
    "source_url",
    "source_product_id",
    "granularity"
]

PRODUCTS = {
    "CPS": {
        "id": "42499",
        "file": "history_CPS.html"
    },
    "TGDD": {
        "id": "40327",
        "file": "history_TGDD.html"
    },
    "VTS": {
        "id": "31146",
        "file": "history_VTS.html"
    }
}


def parse_price(value):
    value = value.strip()

    if not re.fullmatch(
        r"[\d.,\s]+₫?", value
    ):
        return None

    digits = re.sub(r"\D", "", value)

    if not digits:
        return None

    price = int(digits)

    if not 1000000 <= price <= 100000000:
        return None

    return price


def parse_history(retailer, config, days):
    path = DATA_DIR / config["file"]

    if not path.exists():
        raise FileNotFoundError(path)

    soup = BeautifulSoup(
        path.read_text(encoding="utf-8"),
        "html.parser"
    )

    canonical = soup.select_one(
        'link[rel="canonical"]'
    )

    if not canonical:
        raise ValueError("Missing canonical URL")

    source_url = canonical.get("href", "")

    expected = (
        "https://conenmua.vn/san-pham/"
        + config["id"] + "/"
    )

    if not source_url.startswith(expected):
        raise ValueError("Product ID mismatch")

    heading = soup.find("h1")

    if not heading:
        raise ValueError("Missing product title")

    title = heading.get_text(" ", strip=True)

    if not re.search(
        r"\biPhone\s+16\s+128\s*GB\b",
        title,
        re.I
    ):
        raise ValueError("Product title mismatch")

    today = datetime.now(TZ).date()
    cutoff = today - timedelta(days=days - 1)

    records = []
    matched = False

    for table in soup.find_all("table"):

        columns = [
            th.get_text(" ", strip=True)
            for th in table.select("thead th")
        ]

        if columns != [
            "Ngày",
            "Giá bán",
            "Giá gốc shop ghi"
        ]:
            continue

        matched = True

        for tr in table.find_all("tr"):

            cells = tr.find_all("td")

            if len(cells) != 3:
                continue

            values = [
                cell.get_text(" ", strip=True)
                for cell in cells
            ]

            try:
                day = datetime.strptime(
                    values[0],
                    "%d/%m/%Y"
                ).date()

            except ValueError:
                continue

            if not cutoff <= day <= today:
                continue

            price = parse_price(values[1])
            original = parse_price(values[2])

            if price is None:
                continue

            saving = None
            discount = None

            if original and original >= price:
                saving = original - price
                discount = round(
                    saving / original * 100,
                    2
                )

            records.append({
                "timestamp": day.isoformat(),
                "retailer": retailer,
                "sku": "IPHONE-16-128GB-UNKNOWN",
                "model": "iPhone 16",
                "storage": "128GB",
                "color": "UNKNOWN",
                "price": price,
                "original_price": original,
                "advertised_discount_pct": discount,
                "saving_amount": saving,
                "stock": "UNKNOWN",
                "scope": "PRODUCT",
                "source": "CONENMUA",
                "source_url": source_url,
                "source_product_id": config["id"],
                "granularity": "DAILY"
            })

    if not matched:
        raise ValueError("History table not found")

    return records


def record_key(row):
    return (
        str(row["source"]),
        str(row["source_product_id"]),
        str(row["timestamp"])
    )


def export_csv(records):
    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    path = DATA_DIR / "backfill_history.csv"

    with path.open(
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=HEADERS
        )

        writer.writeheader()
        writer.writerows(records)

    print("CSV:", path)


def write_google_sheets(records):
    import gspread

    credentials = os.environ.get(
        "GOOGLE_CREDENTIALS"
    )

    spreadsheet_id = os.environ.get(
        "SPREADSHEET_ID"
    )

    if not credentials:
        credentials = (
            ROOT / "local" / "secrets"
            / "google_credentials.json"
        ).read_text(encoding="utf-8-sig")

    if not spreadsheet_id:
        spreadsheet_id = (
            ROOT / "local" / "secrets"
            / "spreadsheet_id.txt"
        ).read_text(
            encoding="utf-8-sig"
        ).strip()

    client = gspread.service_account_from_dict(
        json.loads(credentials),
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets"
        ]
    )

    book = client.open_by_key(
        spreadsheet_id
    )

    try:
        sheet = book.worksheet(SHEET_NAME)

    except gspread.WorksheetNotFound:
        sheet = book.add_worksheet(
            title=SHEET_NAME,
            rows=1000,
            cols=len(HEADERS)
        )

    existing = sheet.get_all_values()

    if not existing:
        sheet.update(
            range_name="A1",
            values=[HEADERS]
        )
        existing = [HEADERS]

    if existing[0] != HEADERS:
        raise ValueError(
            "Unexpected sheet headers"
        )

    keys = set()

    for values in existing[1:]:
        row = dict(zip(HEADERS, values))

        if all(
            row.get(field)
            for field in [
                "source",
                "source_product_id",
                "timestamp"
            ]
        ):
            keys.add(record_key(row))

    new_rows = []

    for record in records:

        key = record_key(record)

        if key in keys:
            continue

        keys.add(key)

        new_rows.append([
            "" if record[h] is None
            else record[h]
            for h in HEADERS
        ])

    if new_rows:
        sheet.append_rows(
            new_rows,
            value_input_option="RAW"
        )

    print("SHEET:", SHEET_NAME)
    print("INSERTED:", len(new_rows))
    print("DUPLICATES:", len(records) - len(new_rows))


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--days",
        type=int,
        default=90
    )

    parser.add_argument(
        "--write",
        action="store_true"
    )

    args = parser.parse_args()

    if not 1 <= args.days <= 90:
        parser.error("--days must be 1-90")

    print("=" * 60)
    print("BACKFILL HISTORY", VERSION)
    print("DAYS:", args.days)
    print("MODE:", "WRITE" if args.write else "PREVIEW")

    records = []

    for retailer, config in PRODUCTS.items():

        rows = parse_history(
            retailer,
            config,
            args.days
        )

        print("-" * 60)
        print("RETAILER:", retailer)
        print("RECORDS:", len(rows))

        if rows:
            prices = [r["price"] for r in rows]
            dates = [r["timestamp"] for r in rows]

            print("FROM:", min(dates))
            print("TO:", max(dates))
            print("MIN:", min(prices))
            print("MAX:", max(prices))

        records.extend(rows)

    unique = {
        record_key(row): row
        for row in records
    }

    records = sorted(
        unique.values(),
        key=lambda row: (
            row["retailer"],
            row["timestamp"]
        )
    )

    print("=" * 60)
    print("TOTAL RECORDS:", len(records))

    if not records:
        raise RuntimeError(
            "NO VALID HISTORY RECORDS"
        )

    export_csv(records)

    if args.write:
        write_google_sheets(records)

    print("BACKFILL SUCCESS")


if __name__ == "__main__":
    main()
