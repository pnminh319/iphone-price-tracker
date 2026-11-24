
import argparse
import json
import re
import sys
import time

from pathlib import Path
from urllib.parse import urljoin, urlsplit, unquote
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup


VERSION = "V5.3"
USER_AGENT = "iPhonePriceTracker/1.0"

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
LOCAL_DIR = ROOT / "local"
OUTPUT = LOCAL_DIR / "test_data" / "discovered.json"

SOURCES = {
    "TGDD": (
        "https://www.thegioididong.com"
        "/dtdd-apple-iphone"
    ),
    "FPT": (
        "https://fptshop.com.vn"
        "/dien-thoai/apple-iphone"
    )
}

HOSTS = {
    "TGDD": "www.thegioididong.com",
    "FPT": "fptshop.com.vn"
}

PATTERNS = {
    "TGDD": re.compile(
        r"^/dtdd/iphone-[a-z0-9-]+/?$",
        re.I
    ),
    "FPT": re.compile(
        r"^/dien-thoai/iphone-[a-z0-9-]+/?$",
        re.I
    )
}

MODEL_PATTERN = re.compile(
    r"\biphone[\s-]*"
    r"(\d{1,2}e?|air|se|duo)"
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
    r"series|may-cu|máy cũ|hang-cu|"
    r"like.new|refurbished|"
    r"da-qua-su-dung|đã qua sử dụng|"
    r"doi-tra|đổi trả|"
    r"tray-xuoc|trầy xước|"
    r"op-lung|ốp lưng|"
    r"cuong-luc|cường lực|"
    r"phu-kien|phụ kiện|"
    r"99%|98%|95%|"
    r"so-sanh|tin-tuc",
    re.I
)

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": USER_AGENT,
    "Accept-Language": "vi-VN,vi;q=0.9",
    "Accept": "text/html,application/xhtml+xml"
})

ROBOTS = {}
LAST_REQUEST = {}
BLOCKED = set()


# ==========================================
# VALIDATE URL
# ==========================================

def validate_url(retailer, url):

    parsed = urlsplit(url)

    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != HOSTS[retailer]
        or parsed.username
        or parsed.password
    ):
        raise ValueError("INVALID_URL")

    return parsed


# ==========================================
# HTTP REQUEST
# ==========================================

def request(retailer, url, delay=5):

    validate_url(retailer, url)

    if retailer in BLOCKED:
        raise RuntimeError("RETAILER_BLOCKED")

    elapsed = (
        time.monotonic()
        - LAST_REQUEST.get(retailer, 0)
    )

    if elapsed < delay:
        time.sleep(delay - elapsed)

    try:

        response = SESSION.get(
            url,
            timeout=(20, 45),
            allow_redirects=False
        )

    finally:

        LAST_REQUEST[retailer] = (
            time.monotonic()
        )

    print(
        retailer,
        "HTTP:", response.status_code,
        url
    )

    if response.status_code in (401, 403, 429):
        BLOCKED.add(retailer)

    return response


# ==========================================
# ROBOTS.TXT
# ==========================================

def check_robots(retailer, url):

    parsed = validate_url(retailer, url)

    if retailer not in ROBOTS:

        robots_url = (
            f"{parsed.scheme}://"
            f"{parsed.netloc}/robots.txt"
        )

        response = request(
            retailer,
            robots_url
        )

        if response.status_code != 200:

            BLOCKED.add(retailer)

            raise RuntimeError(
                "ROBOTS_UNAVAILABLE"
            )

        parser = RobotFileParser()

        parser.parse(
            response.text.splitlines()
        )

        # Conservative wildcard handling.
        rules = []

        for line in response.text.splitlines():

            line = line.split(
                "#", 1
            )[0].strip()

            if ":" not in line:
                continue

            key, value = line.split(":", 1)

            if (
                key.strip().lower() == "disallow"
                and value.strip()
            ):
                rules.append(value.strip())

        delay = max(
            5,
            parser.crawl_delay(USER_AGENT) or 0,
            parser.crawl_delay("*") or 0
        )

        ROBOTS[retailer] = (
            parser,
            rules,
            delay
        )

    parser, rules, delay = ROBOTS[retailer]

    if not parser.can_fetch(USER_AGENT, url):

        raise RuntimeError(
            "ROBOTS_DISALLOWED"
        )

    target = parsed.path

    if parsed.query:
        target += "?" + parsed.query

    for rule in rules:

        end = rule.endswith("$")

        if end:
            rule = rule[:-1]

        pattern = (
            "^"
            + re.escape(rule).replace(
                r"\*", ".*"
            )
        )

        if end:
            pattern += "$"

        if re.search(pattern, target):

            raise RuntimeError(
                "ROBOTS_DISALLOWED"
            )

    return delay


# ==========================================
# FETCH HTML
# ==========================================

def fetch_html(retailer, url):

    delay = check_robots(
        retailer,
        url
    )

    response = request(
        retailer,
        url,
        delay
    )

    if response.status_code != 200:

        raise RuntimeError(
            f"HTTP_{response.status_code}"
        )

    soup = BeautifulSoup(
        response.content,
        "html.parser"
    )

    title = (
        soup.title.get_text(
            strip=True
        ).lower()
        if soup.title else ""
    )

    if any(x in title for x in (
        "access denied",
        "captcha",
        "just a moment",
        "verify you are human"
    )):

        BLOCKED.add(retailer)

        raise RuntimeError(
            "ACCESS_RESTRICTED"
        )

    return response.content


# ==========================================
# NORMALIZE PRODUCT URL
# ==========================================

def normalize_url(retailer, source, href):

    try:

        url = urljoin(
            source,
            href
        )

        parsed = validate_url(
            retailer,
            url
        )

        # Do not crawl parameterized URLs.
        if parsed.query:
            return None

        path = unquote(parsed.path)

        if EXCLUDE.search(path):
            return None

        if not PATTERNS[retailer].fullmatch(path):
            return None

        return (
            f"https://{HOSTS[retailer]}"
            + path.rstrip("/")
        )

    except ValueError:
        return None


# ==========================================
# NORMALIZE MODEL
# ==========================================

def get_model(text):

    match = MODEL_PATTERN.search(text)

    if not match:
        return None

    model = (
        "iPhone "
        + match.group(1).title()
    )

    if match.group(2):

        model += " " + (
            match.group(2)
            .replace("-", " ")
            .title()
        )

    return model


# ==========================================
# NORMALIZE STORAGE
# ==========================================

def get_storage(text):

    match = STORAGE_PATTERN.search(text)

    if not match:
        return None

    size = int(match.group(1))
    unit = match.group(2).upper()

    if unit == "GB":

        if size in (1024, 2048):
            return f"{size // 1024}TB"

        if size not in (64, 128, 256, 512):
            return None

    if unit == "TB" and size not in (1, 2):
        return None

    return f"{size}{unit}"


# ==========================================
# EXTRACT PRODUCT LINKS
# ==========================================

def extract_products(html, retailer, source):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    found = {}

    for node in soup.select("a[href]"):

        href = node.get(
            "href", ""
        ).strip()

        if not href:
            continue

        url = normalize_url(
            retailer,
            source,
            href
        )

        if not url:
            continue

        text = " ".join([
            node.get_text(
                " ",
                strip=True
            ),
            str(node.get("title", ""))
        ])

        slug = urlsplit(
            url
        ).path.replace("-", " ")

        if EXCLUDE.search(text):
            continue

        model = (
            get_model(slug)
            or get_model(text)
        )

        if not model:
            continue

        storage = (
            get_storage(slug)
            or get_storage(text)
        )

        row = {
            "retailer": retailer,
            "model": model,
            "storage": storage,
            "color": "UNKNOWN",
            "url": url,
            "status": (
                "NEEDS_PRODUCT_VERIFICATION"
                if storage
                else "NEEDS_STORAGE"
            )
        }

        if (
            url not in found
            or (
                storage
                and not found[url]["storage"]
            )
        ):

            found[url] = row

    return sorted(
        found.values(),
        key=lambda x: x["url"]
    )


# ==========================================
# MAIN
# ==========================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Local iPhone Discovery V5.3"
        )
    )

    parser.add_argument(
        "--online",
        action="store_true",
        help="Download live category pages"
    )

    args = parser.parse_args()

    print("=" * 60)
    print("LOCAL DISCOVERY", VERSION)

    print(
        "MODE:",
        "ONLINE" if args.online else "OFFLINE"
    )

    print("=" * 60)

    all_products = []
    failed = 0

    for retailer, source in SOURCES.items():

        try:

            if args.online:

                html = fetch_html(
                    retailer,
                    source
                )

            else:

                path = (
                    LOCAL_DIR
                    / "test_data"
                    / f"{retailer.lower()}.html"
                )

                html = path.read_bytes()

            products = extract_products(
                html,
                retailer,
                source
            )

            print("-" * 60)
            print("RETAILER:", retailer)

            for product in products:

                print(
                    product["model"],
                    product["storage"]
                    or "NEEDS_STORAGE",
                    product["url"],
                    sep=" | "
                )

            known = sum(
                bool(p["storage"])
                for p in products
            )

            print(
                "TOTAL URLS:",
                len(products)
            )

            print(
                "KNOWN STORAGE:",
                known
            )

            print(
                "NEEDS STORAGE:",
                len(products) - known
            )

            if not products:

                raise RuntimeError(
                    "NO_PRODUCT_LINKS"
                )

            all_products.extend(products)

        except Exception as error:

            failed += 1

            print(
                retailer,
                "ERROR:",
                error
            )

    print("=" * 60)
    print("DISCOVERY SUMMARY")
    print("TOTAL:", len(all_products))
    print("FAILED RETAILERS:", failed)

    if failed:

        print(
            "Previous output preserved."
        )

        return 1

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temp = OUTPUT.with_suffix(".tmp")

    temp.write_text(
        json.dumps(
            all_products,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    temp.replace(OUTPUT)

    print("OUTPUT:", OUTPUT)
    print("DISCOVERY SUCCESS")
    print("=" * 60)

    return 0


if __name__ == "__main__":

    sys.exit(main())
