
import json
import sys

from pathlib import Path
from retailers_v52 import parse_html


def main():
    print("LOCAL PRICE TEST V5.2")

    folder = Path(
        sys.argv[1] if len(sys.argv) > 1 else "."
    )

    files = {
        "TGDD": "tgdd-product.html",
        "FPT": "fpt-product.html"
    }

    for retailer, filename in files.items():

        print("=" * 50)
        print("RETAILER:", retailer)

        try:
            html = (
                folder / filename
            ).read_text(encoding="utf-8")

            results = parse_html(
                retailer, html
            )

            for row in results:
                print(json.dumps(
                    row,
                    ensure_ascii=False,
                    indent=2
                ))

            print("TOTAL:", len(results))

        except Exception as e:
            print("ERROR:", str(e))

    print("=" * 50)


if __name__ == "__main__":
    main()
