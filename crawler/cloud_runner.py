
import argparse
import sys

import main as core


VERSION = "V5.3"

CLOUD_RETAILERS = {
    "CPS",
    "HHM",
    "DDV",
    "VTS"
}


def allowed(row):

    retailer = str(
        row.get("retailer", "")
    ).strip().upper()

    return retailer in CLOUD_RETAILERS


def run_prices():

    original_load = core.load_records

    def cloud_load(ws):

        rows = original_load(ws)

        if ws.title == "PRODUCTS":

            return [
                row for row in rows
                if allowed(row)
            ]

        return rows

    core.load_records = cloud_load

    try:
        core.main()
    finally:
        core.load_records = original_load


def run_discovery():

    import discover

    original_get_book = discover.get_book

    class WorksheetProxy:

        def __init__(self, worksheet):

            self.worksheet = worksheet

        def get_all_records(self, *args, **kwargs):

            rows = self.worksheet.get_all_records(
                *args,
                **kwargs
            )

            return [
                row for row in rows
                if allowed(row)
            ]

        def __getattr__(self, name):

            return getattr(
                self.worksheet,
                name
            )

    class BookProxy:

        def __init__(self, book):

            self.book = book

        def worksheet(self, name):

            ws = self.book.worksheet(name)

            if name == "SOURCES":
                return WorksheetProxy(ws)

            return ws

        def __getattr__(self, name):

            return getattr(
                self.book,
                name
            )

    def cloud_book():

        return BookProxy(
            original_get_book()
        )

    discover.get_book = cloud_book

    try:
        discover.main()
    finally:
        discover.get_book = original_get_book


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "mode",
        choices=["discover", "prices"]
    )

    args = parser.parse_args()

    print("=" * 60)
    print("CLOUD RUNNER", VERSION)
    print("MODE:", args.mode)
    print("RETAILERS:", sorted(CLOUD_RETAILERS))
    print("=" * 60)

    if args.mode == "discover":
        run_discovery()
    else:
        run_prices()


if __name__ == "__main__":
    main()
