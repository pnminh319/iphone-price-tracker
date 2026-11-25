import json
import os

from datetime import datetime, timezone, timedelta
from pathlib import Path

import gspread

from main import (
    get_book,
    HEADERS,
    normalize_price,
    load_records,
    now
)


VERSION = "V5.4.2.4"
HISTORY_DAYS = 90

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "data.json"

LOCAL_RETAILERS = {
    "TGDD",
    "FPT"
}


def number(value):

    try:

        if value in ("", None):
            return None

        return float(
            str(value).replace("%", "")
        )

    except (ValueError, TypeError):
        return None


def normalize_row(row):

    record = {
        key: row.get(key, "")
        for key in HEADERS
    }

    record["retailer"] = str(
        record["retailer"]
    ).strip().upper()

    record["sku"] = str(
        record["sku"]
    ).strip().upper()

    record["color"] = (
        str(record["color"]).strip()
        or "UNKNOWN"
    )

    for field in (
        "price",
        "baseline_price",
        "original_price"
    ):

        record[field] = normalize_price(
            record[field]
        )

    for field in (
        "discount_pct",
        "advertised_discount_pct",
        "saving_amount"
    ):

        record[field] = number(
            record[field]
        )

    record["scope"] = (
        row.get("scope")
        or (
            "PRODUCT"
            if record["color"].upper() == "UNKNOWN"
            else "COLOR"
        )
    )

    return record


def read_sheet(
    book,
    name,
    optional=False
):

    try:

        ws = book.worksheet(name)

    except gspread.WorksheetNotFound:

        if optional:
            return []

        raise

    records = []

    for row in load_records(ws):

        row = normalize_row(row)

        if (
            not row["sku"]
            or not row["retailer"]
        ):
            continue

        records.append(row)

    print(
        "READ:",
        name,
        len(records)
    )

    return records


def get_key(row):

    return (
        row["retailer"],
        row["sku"]
    )


def timestamp_key(row):

    try:

        value = datetime.fromisoformat(
            str(row["timestamp"])
            .replace("Z", "+00:00")
        )

        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone(timedelta(hours=7)))
        return value.timestamp()

    except (ValueError, TypeError):
        return 0


def merge_current(cloud, local):

    merged = {}

    for row in cloud:

        key = get_key(row)

        if (
            key not in merged
            or timestamp_key(row)
            > timestamp_key(merged[key])
        ):

            merged[key] = row

    for row in local:

        if row["retailer"] not in LOCAL_RETAILERS:
            continue

        key = get_key(row)

        merged[key] = row

    return sorted(
        merged.values(),
        key=lambda row: (
            row["model"],
            row["storage"],
            row["retailer"],
            row["color"]
        )
    )


def merge_history(cloud, local):

    merged = {}

    for row in cloud + local:

        key = (
            row["retailer"],
            row["sku"],
            row["timestamp"]
        )

        merged[key] = row

    return sorted(
        merged.values(),
        key=timestamp_key
    )


def read_backfill(book):
    try:
        ws = book.worksheet("BACKFILL_PRICE_HISTORY")
    except gspread.WorksheetNotFound:
        return []

    records = []
    if os.environ.get("ENABLE_BACKFILL", "").lower() != "true":
        print("BACKFILL DISABLED: permission not confirmed")
        return []
    for raw in load_records(ws):
        if raw.get("source") != "CONENMUA":
            continue
        if not str(raw.get("source_url", "")).startswith("https://"):
            continue
        row = normalize_row(raw)
        if not all(row.get(k) for k in ("sku", "model", "storage", "price")):
            continue
        if row["retailer"] not in {"TGDD", "FPT", "CPS", "HHM", "DDV", "VTS", "SDK"}:
            continue
        row["stock"] = "UNKNOWN"
        row["scope"] = "PRODUCT"
        row["color"] = "UNKNOWN"
        row["granularity"] = "DAILY"
        row["source"] = "BACKFILL"
        row["source_name"] = str(raw.get("source", ""))
        row["source_url"] = str(raw["source_url"])
        records.append(row)
    print("READ: BACKFILL_PRICE_HISTORY", len(records))
    return records


def prepare_history(cloud, local, backfill, end):
    merged = {}
    start = end - HISTORY_DAYS * 86400
    for row in backfill + cloud + local:
        t = timestamp_key(row)
        if not start <= t <= end:
            continue
        row = dict(row)
        row["timestamp"] = datetime.fromtimestamp(t, timezone.utc).isoformat()
        row.setdefault("source", "CRAWLER")
        key = (row["retailer"], row["sku"], t)
        merged[key] = row
    return sorted(merged.values(), key=timestamp_key)


def main():

    print("=" * 60)
    print("DASHBOARD EXPORT", VERSION)

    book = get_book()

    cloud_current = read_sheet(
        book,
        "CURRENT_PRICE"
    )

    cloud_history = read_sheet(
        book,
        "PRICE_HISTORY"
    )

    local_current = read_sheet(
        book,
        "LOCAL_CURRENT_PRICE",
        optional=True
    )

    local_history = read_sheet(
        book,
        "LOCAL_PRICE_HISTORY",
        optional=True
    )

    current = merge_current(
        cloud_current,
        local_current
    )

    backfill = read_backfill(book)
    history = prepare_history(
        cloud_history, local_history, backfill,
        datetime.now(timezone.utc).timestamp()
    )

    if not current:

        raise RuntimeError(
            "NO_CURRENT_DATA"
        )

    payload = {
        "version": VERSION,
        "exported_at": now(),
        "current": current,
        "history_days": HISTORY_DAYS,
        "history": history
    }

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temp = OUTPUT.with_suffix(".tmp")

    temp.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            allow_nan=False
        ),
        encoding="utf-8"
    )

    temp.replace(OUTPUT)

    print("CURRENT:", len(current))
    print("HISTORY:", len(history))
    print("OUTPUT:", OUTPUT)
    print("EXPORT SUCCESS")
    print("=" * 60)


if __name__ == "__main__":
    main()
