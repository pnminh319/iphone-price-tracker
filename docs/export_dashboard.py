
import json
from datetime import datetime, timezone
from pathlib import Path
from main import get_book

book = get_book()

result = {
    "exported_at": datetime.now(
        timezone.utc
    ).isoformat()
}

fields = [
    "timestamp", "retailer", "sku",
    "model", "storage", "color",
    "price", "baseline_price",
    "discount_pct", "stock", "url"
]

for sheet, key in [
    ("CURRENT_PRICE", "current"),
    ("PRICE_HISTORY", "history")
]:
    result[key] = [
        {k: row.get(k, "") for k in fields}
        for row in book.worksheet(
            sheet
        ).get_all_records()
    ]

Path("docs").mkdir(exist_ok=True)

Path("docs/data.json").write_text(
    json.dumps(result, ensure_ascii=False),
    encoding="utf-8"
)

print("Dashboard data exported")
