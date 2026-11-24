
import re

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from main import (
    check_url,
    check_robots,
    get_jsonld_price,
    USER_AGENT
)


# ==========================================
# ORIGINAL PRICE SELECTORS
# ==========================================

ORIGINAL_PRICE_SELECTORS = {
    "CPS": [
        ".product__price--through",
        ".box-price-present + .box-price-through",
        ".box-price-through"
    ],

    "HHM": [],
    "DDV": [],
    "TGDD": [],
    "FPT": [],
    "VTS": []
}


# ==========================================
# NORMALIZE DISPLAY PRICE
# ==========================================

def parse_display_price(text):

    if not text:
        return None

    text = str(text).strip()

    if not re.fullmatch(
        r"[\d.,\s]+(?:đ|₫|VND)?\s*",
        text,
        re.I
    ):
        return None

    digits = re.sub(r"\D", "", text)

    if not digits:
        return None

    price = int(digits)

    if 1000000 <= price <= 100000000:
        return price

    return None


# ==========================================
# READ ORIGINAL PRICE
# ==========================================

def read_original_price(soup, retailer, price):

    candidates = set()

    selectors = ORIGINAL_PRICE_SELECTORS.get(
        retailer, []
    )

    for selector in selectors:

        elements = soup.select(selector)

        for element in elements:

            text = element.get_text(
                " ", strip=True
            )

            value = parse_display_price(text)

            print(
                "ORIGINAL PRICE CANDIDATE:",
                selector,
                text
            )

            if value and value > price:
                candidates.add(value)

    if len(candidates) == 1:

        original_price = candidates.pop()

        print(
            "ORIGINAL PRICE:",
            original_price
        )

        return original_price

    if len(candidates) > 1:

        print(
            "AMBIGUOUS ORIGINAL PRICE:",
            sorted(candidates)
        )

    else:

        print("ORIGINAL_PRICE_NOT_FOUND")

    return None


# ==========================================
# PRICE DIAGNOSTICS
# ==========================================

def print_price_candidates(soup):

    selectors = [
        '[itemprop="price"]',
        'meta[property="product:price:amount"]',
        ".product-price",
        ".price-current",
        ".sale-price",
        ".price",
        ".old-price",
        ".price-old",
        ".price-original",
        ".product__price--through",
        ".box-price-through",
        "del",
        "s",
        "strike"
    ]

    for selector in selectors:

        elements = soup.select(selector)

        for element in elements[:3]:

            value = (
                element.get("content")
                or element.get_text(
                    " ", strip=True
                )
            )

            print(
                "PRICE CANDIDATE:",
                selector,
                str(value)[:150]
            )


# ==========================================
# READ PRODUCT PRICE
# ==========================================

def read_price(product):

    retailer = str(
        product["retailer"]
    ).strip().upper()

    url = str(
        product["url"]
    ).strip()

    check_url(retailer, url)
    check_robots(url)

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        try:

            page = browser.new_page(
                locale="vi-VN",
                timezone_id="Asia/Ho_Chi_Minh",
                user_agent=USER_AGENT
            )

            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=45000
            )

            if response and response.status >= 400:

                raise ValueError(
                    f"HTTP_{response.status}"
                )

            check_url(retailer, page.url)
            check_robots(page.url)

            page.wait_for_timeout(4000)

            soup = BeautifulSoup(
                page.content(),
                "html.parser"
            )

            title = (
                soup.title.get_text(
                    " ", strip=True
                )
                if soup.title
                else ""
            )

            print("PAGE TITLE:", title)

            # Read current price from JSON-LD

            try:

                price, stock = get_jsonld_price(
                    soup,
                    product
                )

            except ValueError as error:

                print("JSONLD:", str(error))

                print_price_candidates(soup)

                raise ValueError(
                    "PRICE_REQUIRES_VALIDATION"
                )

            print("CURRENT PRICE:", price)

            # Read advertised original price

            original_price = read_original_price(
                soup,
                retailer,
                price
            )

            if original_price is None:

                print_price_candidates(soup)

            if original_price:

                saving = original_price - price

                discount = round(
                    saving / original_price * 100,
                    2
                )

                print("SAVING:", saving)
                print("ADVERTISED DISCOUNT:", discount)

            print("STOCK:", stock)

            return price, stock, original_price

        finally:

            browser.close()
