
import re
import time
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from main import get_book, check_url, check_robots
from main import now, active, USER_AGENT

PRODUCT_HEADERS = [
    "sku", "model", "storage", "color",
    "retailer", "url", "active"
]

MODEL_PATTERN = re.compile(
    r"\biphone[\s-]*"
    r"(\d{1,2}e?|air|duo|se)"
    r"(?:[\s-]+(pro[\s-]+max|pro|plus|mini|air))?"
    r"\b",
    re.I
)

STORAGE_PATTERN = re.compile(
    r"\b(64|128|256|512|1024|2048|1|2)"
    r"[\s-]*(gb|tb)\b",
    re.I
)

EXCLUDE = re.compile(
    r"series|máy cũ|like.new|đã qua sử dụng|"
    r"trầy xước|cũ đẹp|99%|98%|95%|"
    r"ốp lưng|cường lực|phụ kiện|"
    r"đổi trả|refurbished|used",
    re.I
)


def parse_product(text, url, retailer):
    slug = urlparse(url).path.replace("-", " ")
    combined = text + " " + slug

    if EXCLUDE.search(combined):
        return None

    model_match = MODEL_PATTERN.search(combined)
    storage_match = STORAGE_PATTERN.search(combined)

    if not model_match or not storage_match:
        return None

    model = "iPhone " + model_match.group(1).title()

    if model_match.group(2):
        model += " " + model_match.group(2).replace(
            "-", " "
        ).title()

    size = int(storage_match.group(1))
    unit = storage_match.group(2).upper()

    if unit == "GB" and size in (1024, 2048):
        size = size // 1024
        unit = "TB"

    storage = f"{size}{unit}"

    if size in (1, 2) and unit == "GB":
        return None

    color = "UNKNOWN"

    sku = (
        model.upper().replace(" ", "-")
        + "-" + storage
        + "-" + color
    )

    return [
        sku,
        model,
        storage,
        color,
        retailer,
        url,
        "TRUE"
    ]


def discover(page, retailer, source):
    check_url(retailer, source)
    check_robots(source)

    response = page.goto(
        source,
        wait_until="domcontentloaded",
        timeout=45000
    )

    if response and response.status >= 400:
        raise RuntimeError(
            f"HTTP_{response.status}"
        )

    check_url(retailer, page.url)
    check_robots(page.url)

    page.wait_for_timeout(4000)

    for _ in range(6):
        page.evaluate(
            "window.scrollBy(0, window.innerHeight)"
        )
        page.wait_for_timeout(1500)

    soup = BeautifulSoup(
        page.content(), "html.parser"
    )

    found = {}

    for a in soup.select("a[href]"):
        url = urljoin(
            page.url, a.get("href", "")
        )

        url = url.split("#")[0]

        if url == source:
            continue

        try:
            check_url(retailer, url)
        except ValueError:
            continue

        text = a.get_text(" ", strip=True)
        text += " " + a.get("title", "")

        product = parse_product(
            text, url, retailer
        )

        if product:
            found[url] = product

    return list(found.values())


def main():
    book = get_book()

    sources = book.worksheet(
        "SOURCES"
    ).get_all_records()

    products_ws = book.worksheet("PRODUCTS")
    log_ws = book.worksheet("CRAWL_LOG")

    headers = products_ws.row_values(1)

    if not headers:
        products_ws.append_row(PRODUCT_HEADERS)

    elif headers != PRODUCT_HEADERS:
        raise ValueError(
            "INVALID_PRODUCTS_HEADERS"
        )

    old = products_ws.get_all_records()

    existing_urls = {
        str(row["url"]).strip()
        for row in old
    }

    new_rows = []
    log_rows = []

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page(
            locale="vi-VN",
            timezone_id="Asia/Ho_Chi_Minh",
            user_agent=USER_AGENT
        )

        for source in sources:

            if not active(source.get("active")):
                continue

            retailer = str(
                source["retailer"]
            ).strip().upper()

            url = str(source["url"]).strip()

            try:
                products = discover(
                    page, retailer, url
                )

                count = 0

                for product in products:

                    if product[5] in existing_urls:
                        continue

                    # New links require validation
                    # before entering price tracking.
                    product[6] = "FALSE"

                    new_rows.append(product)
                    existing_urls.add(product[5])
                    count += 1

                message = (
                    f"DISCOVERED: {len(products)}"
                    f" | NEW: {count}"
                )

                print(retailer, message)

                log_rows.append([
                    now(), retailer, url, message
                ])

            except Exception as e:
                message = str(e)[:300]

                print(retailer, "ERROR", message)

                log_rows.append([
                    now(), retailer, url,
                    "DISCOVERY_ERROR: " + message
                ])

            time.sleep(3)

        browser.close()

    if new_rows:
        products_ws.append_rows(
            new_rows,
            value_input_option="RAW"
        )

    if log_rows:
        log_ws.append_rows(
            log_rows,
            value_input_option="RAW"
        )

    print("TOTAL NEW URLS:", len(new_rows))


if __name__ == "__main__":
    main()
