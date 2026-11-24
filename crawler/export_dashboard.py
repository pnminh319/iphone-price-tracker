
import json

from datetime import datetime, timezone, timedelta
from pathlib import Path

from main import get_book, HEADERS, normalize_price


VERSION = "V4.2"

VN_TZ = timezone(timedelta(hours=7))

ROOT_DIR = Path(__file__).resolve().parent.parent

DOCS_DIR = ROOT_DIR / "docs"

OUTPUT_FILE = DOCS_DIR / "data.json"


PRICE_FIELDS = [
    "price",
    "baseline_price",
    "original_price",
    "saving_amount"
]

PERCENT_FIELDS = [
    "discount_pct",
    "advertised_discount_pct"
]


def now():

    return datetime.now(VN_TZ).isoformat(
        timespec="seconds"
    )


def read_sheet(book, sheet_name):

    ws = book.worksheet(sheet_name)

    rows = ws.get_all_records(
        value_render_option="UNFORMATTED_VALUE"
    )

    result = []

    for row in rows:

        if not row.get("sku"):
            continue

        if not row.get("retailer"):
            continue

        record = {
            field: row.get(field, "")
            for field in HEADERS
        }

        # Normalize price fields.

        for field in PRICE_FIELDS:

            value = record.get(field)

            if field == "saving_amount":

                try:
                    if isinstance(value, (int, float)):
                        record[field] = int(value)
                    elif value:
                        text = str(value)
                        for char in [".", ",", " ", "đ", "₫"]:
                            text = text.replace(char, "")
                        record[field] = int(text)
                    else:
                        record[field] = None

                except (ValueError, TypeError):
                    record[field] = None

            else:

                record[field] = normalize_price(value)

        # Normalize discount percentages.

        for field in PERCENT_FIELDS:

            value = record.get(field)

            try:

                text = str(value).replace(
                    "%", ""
                ).replace(",", ".").strip()

                record[field] = (
                    float(text) if text else None
                )

            except (ValueError, TypeError):

                record[field] = None

        result.append(record)

    print(
        f"READ {sheet_name}: {len(result)} rows"
    )

    return result


def main():

    print("=" * 50)
    print("EXPORT DASHBOARD", VERSION)
    print("START:", now())
    print("=" * 50)

    book = get_book()

    current = read_sheet(
        book,
        "CURRENT_PRICE"
    )

    history = read_sheet(
        book,
        "PRICE_HISTORY"
    )

    data = {
        "version": VERSION,
        "exported_at": now(),
        "current": current,
        "history": history
    }

    DOCS_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    temp_file = DOCS_DIR / "data.tmp"

    temp_file.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
            default=str,
            allow_nan=False
        ),
        encoding="utf-8"
    )

    temp_file.replace(OUTPUT_FILE)

    print("=" * 50)
    print("EXPORT SUCCESS")
    print("CURRENT PRODUCTS:", len(current))
    print("HISTORY RECORDS:", len(history))
    print("OUTPUT:", OUTPUT_FILE)
    print("UPDATED:", now())
    print("=" * 50)


if __name__ == "__main__":

    main()
