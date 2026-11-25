import re
import time
import requests

from urllib.parse import (
    urljoin,
    urlparse,
    urlunparse,
    parse_qsl,
    urlencode
)

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from main import (
    get_book,
    check_url,
    check_robots,
    now,
    active,
    USER_AGENT
)

from price_reader import read_price


VERSION = "V5.1"

AUTO_ACTIVATE = True

MAX_NEW_PER_RETAILER = 40

MIN_REQUESTS_PRODUCTS = 5

PRODUCT_HEADERS = [
    "sku",
    "model",
    "storage",
    "color",
    "retailer",
    "url",
    "active"
]

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": USER_AGENT,
    "Accept-Language": "vi-VN,vi;q=0.9"
})


# ==========================================
# PRODUCT PATTERNS
# ==========================================

MODEL_PATTERN = re.compile(
    r"\biphone[\s-]*"
    r"(\d{1,2}e?|air|duo|se)"
    r"(?:[\s-]+"
    r"(pro[\s-]+max|pro|plus|mini|air))?"
    r"\b",
    re.I
)

STORAGE_PATTERN = re.compile(
    r"\b(64|128|256|512|1024|2048|1|2)"
    r"[\s-]*(gb|tb)\b",
    re.I
)

EXCLUDE = re.compile(
    r"series|máy cũ|may-cu|"
    r"hang-cu|like.new|"
    r"đã qua sử dụng|da-qua-su-dung|"
    r"trầy xước|tray-xuoc|"
    r"cũ đẹp|cu-dep|"
    r"99%|98%|95%|"
    r"ốp lưng|op-lung|"
    r"cường lực|cuong-luc|"
    r"phụ kiện|phu-kien|"
    r"đổi trả|doi-tra|"
    r"refurbished|used|"
    r"so-sanh|tin-tuc|"
    r"dchannel|bai-viet|"
    r"tra-gop-cu|"
    r"chuong-trinh",
    re.I
)

URL_PATTERNS = {
    "SDK": re.compile(r"^/(?:(?:dien-thoai-)?iphone-[^/]+|iphone\d[^/]*)(?:/)?$", re.I),
    "CPS": re.compile(
        r"^/iphone-[^/]+\.html$",
        re.I
    ),

    "HHM": re.compile(
        r"^/dien-thoai-di-dong/"
        r"(?:apple-)?iphone-[^/]+/?$",
        re.I
    ),

    "DDV": re.compile(
        r"^/dien-thoai/"
        r"iphone-[^/]+\.html$",
        re.I
    ),

    "VTS": re.compile(
        r"^/dien-thoai/"
        r"iphone-[^/]+-pid\d+\.html$",
        re.I
    ),

    "TGDD": re.compile(
        r"^/dtdd/iphone-[^/]+/?$",
        re.I
    ),

    "FPT": re.compile(
        r"^/dien-thoai/iphone-[^/]+/?$",
        re.I
    )
}

TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "ref",
    "source",
    "campaign",
    "affiliate"
}


# ==========================================
# ACCESS VALIDATION
# ==========================================

def validate_access(retailer, url):

    result = check_url(retailer, url)

    if result is False:
        raise RuntimeError("INVALID_URL")

    result = check_robots(url)

    if result is False:
        raise RuntimeError("ROBOTS_NOT_ALLOWED")


# ==========================================
# NORMALIZE URL
# ==========================================

def normalize_url(url):

    parsed = urlparse(url)

    params = []

    for key, value in parse_qsl(
        parsed.query,
        keep_blank_values=True
    ):

        lower = key.lower()

        if lower.startswith("utm_"):
            continue

        if lower in TRACKING_PARAMS:
            continue

        params.append((key, value))

    return urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path.rstrip("/"),
        "",
        urlencode(sorted(params)),
        ""
    ))


# ==========================================
# PRODUCT URL FILTER
# ==========================================

def valid_product_url(retailer, url):

    try:

        result = check_url(retailer, url)

        if result is False:
            return False

    except Exception:
        return False

    parsed = urlparse(url)

    if parsed.scheme != "https":
        return False

    path = parsed.path

    if EXCLUDE.search(path):
        return False

    pattern = URL_PATTERNS.get(retailer)

    if pattern is None:
        return False

    return bool(pattern.fullmatch(path))


# ==========================================
# PARSE MODEL AND STORAGE
# ==========================================

def parse_product(text, url, retailer):

    slug = urlparse(url).path.replace("-", " ")

    if EXCLUDE.search(text + " " + slug):
        return None

    # URL has priority over anchor text.

    model_match = (
        MODEL_PATTERN.search(slug)
        or MODEL_PATTERN.search(text)
    )

    storage_match = (
        STORAGE_PATTERN.search(slug)
        or STORAGE_PATTERN.search(text)
    )

    if not model_match or not storage_match:
        return None

    model = (
        "iPhone "
        + model_match.group(1).title()
    )

    if model_match.group(2):

        model += (
            " "
            + model_match.group(2)
            .replace("-", " ")
            .title()
        )

    size = int(storage_match.group(1))

    unit = storage_match.group(2).upper()

    if unit == "GB" and size in (1024, 2048):

        size = size // 1024
        unit = "TB"

    if size in (1, 2) and unit == "GB":
        return None

    if unit == "TB" and size not in (1, 2):
        return None

    storage = f"{size}{unit}"

    color = "UNKNOWN"

    sku = (
        model.upper().replace(" ", "-")
        + "-"
        + storage
        + "-"
        + color
    )

    return [
        sku,
        model,
        storage,
        color,
        retailer,
        url,
        "FALSE"
    ]


# ==========================================
# EXTRACT PRODUCT LINKS
# ==========================================

def extract_products(html, retailer, source):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    found = {}

    for a in soup.select(".product-title a[href]" if retailer == "SDK" else "a[href]"):

        href = a.get("href", "").strip()

        if not href:
            continue

        url = normalize_url(
            urljoin(source, href)
        )

        if url == normalize_url(source):
            continue

        if not valid_product_url(
            retailer,
            url
        ):
            continue

        text = a.get_text(
            " ",
            strip=True
        )

        text += " " + str(
            a.get("title", "")
        )

        product = parse_product(
            text,
            url,
            retailer
        )

        if product:

            found[url] = product

    return list(found.values())


# ==========================================
# REQUESTS DISCOVERY
# ==========================================

def discover_requests(retailer, source):

    validate_access(retailer, source)

    response = SESSION.get(
        source,
        timeout=(15, 40),
        allow_redirects=False
    )

    print(
        retailer,
        "REQUESTS_HTTP:",
        response.status_code
    )

    if response.status_code != 200:

        raise RuntimeError(
            f"HTTP_{response.status_code}"
        )

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    title = (
        soup.title.get_text(strip=True).lower()
        if soup.title else ""
    )

    if any(x in title for x in [
        "access denied",
        "verify you are human",
        "captcha",
        "just a moment"
    ]):

        raise RuntimeError(
            "ACCESS_RESTRICTED"
        )

    products = extract_products(
        response.text,
        retailer,
        source
    )

    print(
        retailer,
        "REQUESTS_PRODUCTS:",
        len(products)
    )

    return products


# ==========================================
# PLAYWRIGHT DISCOVERY
# ==========================================

def discover_playwright(
    browser,
    retailer,
    source
):

    validate_access(retailer, source)

    context = browser.new_context(
        locale="vi-VN",
        timezone_id="Asia/Ho_Chi_Minh",
        user_agent=USER_AGENT
    )

    def guard(route):

        request = route.request

        if request.is_navigation_request():

            if request.url != source:

                route.abort()
                return

        if request.resource_type in (
            "image",
            "media",
            "font"
        ):

            route.abort()
            return

        route.continue_()

    context.route("**/*", guard)

    page = context.new_page()

    try:

        response = page.goto(
            source,
            wait_until="domcontentloaded",
            timeout=45000
        )

        if response is None:

            raise RuntimeError(
                "NO_BROWSER_RESPONSE"
            )

        print(
            retailer,
            "PLAYWRIGHT_HTTP:",
            response.status
        )

        if response.status != 200:

            raise RuntimeError(
                f"HTTP_{response.status}"
            )

        validate_access(
            retailer,
            page.url
        )

        page.wait_for_timeout(3000)

        for _ in range(4):

            page.evaluate(
                "window.scrollBy("
                "0, window.innerHeight)"
            )

            page.wait_for_timeout(1000)

        title = page.title().lower()

        if any(x in title for x in [
            "access denied",
            "verify you are human",
            "captcha",
            "just a moment"
        ]):

            raise RuntimeError(
                "ACCESS_RESTRICTED"
            )

        products = extract_products(
            page.content(),
            retailer,
            page.url
        )

        print(
            retailer,
            "PLAYWRIGHT_PRODUCTS:",
            len(products)
        )

        return products

    finally:

        context.close()


# ==========================================
# HYBRID DISCOVERY
# ==========================================

def discover(browser, retailer, source):

    # Requests is always the first method.
    # Do not use browser fallback after
    # an access restriction or HTTP error.

    products = discover_requests(
        retailer,
        source
    )

    # VTS already returned HTTP 403
    # during browser testing.

    if retailer == "VTS":

        return products, "REQUESTS"

    if len(products) >= MIN_REQUESTS_PRODUCTS:

        return products, "REQUESTS"

    print(
        retailer,
        "TRY_PLAYWRIGHT"
    )

    try:

        browser_products = (
            discover_playwright(
                browser,
                retailer,
                source
            )
        )

        merged = {
            product[5]: product
            for product in products
        }

        for product in browser_products:

            merged[product[5]] = product

        return (
            list(merged.values()),
            "HYBRID"
        )

    except Exception as e:

        print(
            retailer,
            "PLAYWRIGHT_ERROR:",
            str(e)[:300]
        )

        return products, "REQUESTS"


# ==========================================
# VALIDATE NEW PRODUCT
# ==========================================

def validate_product(row):

    product = dict(
        zip(PRODUCT_HEADERS, row)
    )

    try:

        result = read_price(product)

        if not result:
            return False

        price = result[0]

        if price is None:
            return False

        price = float(price)

        return (
            1000000 <= price <= 150000000
        )

    except Exception as e:

        print(
            "VALIDATION_ERROR:",
            product["url"],
            str(e)[:200]
        )

        return False


# ==========================================
# LOG ROW
# ==========================================

def make_log(headers, retailer, url, message):

    values = {
        "timestamp": now(),
        "retailer": retailer,
        "url": url,
        "status": message,
        "message": message
    }

    return [
        values.get(
            str(header).strip().lower(),
            ""
        )
        for header in headers
    ]


# ==========================================
# MAIN
# ==========================================

def main():

    print(
        "=" * 60
    )

    print(
        "DISCOVERY VERSION:",
        VERSION
    )

    book = get_book()

    sources = book.worksheet(
        "SOURCES"
    ).get_all_records()

    products_ws = book.worksheet(
        "PRODUCTS"
    )

    log_ws = book.worksheet(
        "CRAWL_LOG"
    )

    headers = products_ws.row_values(1)

    if not headers:

        products_ws.append_row(
            PRODUCT_HEADERS
        )

    elif headers != PRODUCT_HEADERS:

        raise ValueError(
            "INVALID_PRODUCTS_HEADERS"
        )

    log_headers = log_ws.row_values(1)

    if not log_headers:

        log_headers = [
            "timestamp",
            "retailer",
            "url",
            "status"
        ]

        log_ws.append_row(log_headers)

    old = products_ws.get_all_records()

    existing_urls = {
        normalize_url(
            str(row.get("url", "")).strip()
        )
        for row in old
        if row.get("url")
    }

    new_rows = []
    log_rows = []

    total_active = 0
    total_pending = 0

    retailer_new_counts = {}

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        try:

            for source in sources:

                if not active(
                    source.get("active")
                ):

                    continue

                retailer = str(
                    source.get("retailer", "")
                ).strip().upper()

                url = str(
                    source.get("url", "")
                ).strip()

                print("=" * 60)

                print(
                    "RETAILER:",
                    retailer
                )

                try:

                    products, method = discover(
                        browser,
                        retailer,
                        url
                    )

                    count = 0
                    activated = 0
                    pending = 0

                    for product in products:

                        product_url = product[5]

                        if product_url in existing_urls:
                            continue

                        retailer_count = (
                            retailer_new_counts.get(
                                retailer, 0
                            )
                        )

                        if (
                            retailer_count
                            >= MAX_NEW_PER_RETAILER
                        ):

                            break

                        verified = False

                        if AUTO_ACTIVATE:

                            verified = (
                                validate_product(
                                    product
                                )
                            )

                        if verified:

                            product[6] = "TRUE"

                            activated += 1
                            total_active += 1

                        else:

                            product[6] = "FALSE"

                            pending += 1
                            total_pending += 1

                        new_rows.append(product)

                        existing_urls.add(
                            product_url
                        )

                        retailer_new_counts[
                            retailer
                        ] = retailer_count + 1

                        count += 1

                        print(
                            "NEW_PRODUCT:",
                            product[0],
                            product[6]
                        )

                        time.sleep(2)

                    message = (
                        f"METHOD: {method}"
                        f" | DISCOVERED: {len(products)}"
                        f" | NEW: {count}"
                        f" | ACTIVE: {activated}"
                        f" | PENDING: {pending}"
                    )

                    print(
                        retailer,
                        message
                    )

                    log_rows.append(
                        make_log(
                            log_headers,
                            retailer,
                            url,
                            message
                        )
                    )

                except Exception as e:

                    message = str(e)[:300]

                    print(
                        retailer,
                        "ERROR:",
                        message
                    )

                    log_rows.append(
                        make_log(
                            log_headers,
                            retailer,
                            url,
                            "DISCOVERY_ERROR: "
                            + message
                        )
                    )

                time.sleep(3)

        finally:

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

    print("=" * 60)

    print("DISCOVERY V5.1 SUMMARY")

    print(
        "TOTAL NEW URLS:",
        len(new_rows)
    )

    print(
        "TOTAL AUTO ACTIVATED:",
        total_active
    )

    print(
        "TOTAL PENDING:",
        total_pending
    )

    print("=" * 60)


if __name__ == "__main__":
    main()
