
import re
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from main import (
    check_url,
    check_robots,
    get_jsonld_price,
    USER_AGENT
)


ORIGINAL_PRICE_SELECTORS = {
    "CPS": [
        ".product__price--through"
    ],
    "HHM": [],
    "DDV": [],
    "TGDD": [],
    "FPT": [],
    "VTS": []
}


def read_original_price(soup, retailer, price):
    candidates = set()

    selectors = ORIGINAL_PRICE_SELECTORS.get(
        retailer, []
    )

    for selector in selectors:
        for element in soup.select(selector):
            text = element.get_text(
                " ", strip=True
            )

            print(
                "ORIGINAL PRICE CANDIDATE:",
                selector, text
            )

            if not re.fullmatch(
                r"\s*[\d.,\s]+(?:đ|₫|VND)?\s*",
                text,
                re.I
            ):
                continue

            digits = re.sub(r"\D", "", text)

            if not digits:
                continue

            value = int(digits)

            if price <= value <= 100000000:
                candidates.add(value)

    if len(candidates) == 1:
        return candidates.pop()

    print("ORIGINAL_PRICE_NOT_VERIFIED")

    return None

def read_price(product):
    retailer = str(
        product["retailer"]
    ).strip().upper()

    url = str(product["url"]).strip()

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

            page.wait_for_timeout(3000)

            soup = BeautifulSoup(
                page.content(),
                "html.parser"
            )

            title = soup.title.get_text(
                " ", strip=True
            ) if soup.title else ""

            print("PAGE TITLE:", title)

            try:
                price, stock = get_jsonld_price(
                    soup, product
                )

                print("JSONLD PRICE:", price)

                return price, stock

            except ValueError as error:
                print("JSONLD:", str(error))

            # Diagnostic information only.
            # Do not save unverified prices.

            selectors = [
                '[itemprop="price"]',
                'meta[property="product:price:amount"]',
                '.product-price',
                '.price-current',
                '.sale-price',
                '.price'
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
                        value[:150]
                    )

            raise ValueError(
                "PRICE_REQUIRES_VALIDATION"
            )

        finally:
            browser.close()
