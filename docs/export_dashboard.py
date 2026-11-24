
import json

from datetime import datetime, timezone
from pathlib import Path

from main import get_book, HEADERS

book = get_book()

result = {
    "exported_at": datetime.now(
        timezone.utc
    ).isoformat()
}

for sheet, key in [
    ("CURRENT_PRICE", "current"),
    ("PRICE_HISTORY", "history")
]:

    result[key] = [
        {
            field: row.get(field, "")
            for field in HEADERS
        }
        for row in book.worksheet(
            sheet
        ).get_all_records()
    ]

Path("docs").mkdir(
    exist_ok=True
)

Path("docs/data.json").write_text(
    json.dumps(
        result,
        ensure_ascii=False
    ),
    encoding="utf-8"
)

print("Dashboard data exported")
print("Current products:", len(result["current"]))
print("History records:", len(result["history"]))
