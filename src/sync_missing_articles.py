from dotenv import load_dotenv
load_dotenv()

import argparse
import asyncio
import csv
import html
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Set, Tuple
from urllib.parse import urlencode, urljoin

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


# Represents a public article extracted from the public website
@dataclass(frozen=True)
class PublicArticle:
    title: str
    url: str
    date_text: str


# Regex to collapse repeated whitespace
_whitespace_re = re.compile(r"\s+")


def normalize_title(raw: str) -> str:
    """
    Normalize a title to ensure consistent matching and CSV saving:
    - Decode HTML entities
    - Replace non-breaking spaces
    - Strip leading/trailing whitespace
    - Strip wrapping quote characters
    - Collapse internal whitespace
    """
    if raw is None:
        return ""

    s = html.unescape(raw)

    # Replace non-breaking space with normal space
    s = s.replace("\u00a0", " ")

    # First trim outer whitespace
    s = s.strip()

    # Strip common quote characters from both ends
    s = s.strip('\'"“”‘’„‟‹›«»')

    # Collapse any internal repeated whitespace
    s = _whitespace_re.sub(" ", s).strip()

    return s


def public_page_url(base_list_url: str, page_num: int) -> str:
    """
    Generate the pagination URL for the public listing.
    Page 1 returns the base URL.
    """
    if page_num <= 1:
        return base_list_url if base_list_url.endswith("/") else base_list_url + "/"
    base = base_list_url if base_list_url.endswith("/") else base_list_url + "/"
    return urljoin(base, f"page/{page_num}/")


async def fetch_public_articles(base_list_url: str, page_num: int, timeout_s: float = 30.0) -> List[PublicArticle]:
    """
    Scrape one public listing page, extract article titles, URLs, and displayed dates.
    """
    url = public_page_url(base_list_url, page_num)
    print(f"Fetching public page {page_num}: {url}")

    async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    main = soup.select_one("section#main")
    if not main:
        print(f"No content found on page {page_num}")
        return []

    items: List[PublicArticle] = []
    article_nodes = main.select("#post-area article h2.entry-title a")
    print(f"Found {len(article_nodes)} articles on page {page_num}")

    for a in article_nodes:
        title_raw = a.get_text(strip=True)
        title = normalize_title(title_raw)
        href = (a.get("href") or "").strip()

        date_text = ""
        article = a.find_parent("article")
        if article:
            date_node = article.select_one("footer .right .posted-on a")
            if date_node:
                date_text = normalize_title(date_node.get_text(strip=True))

        if title and href:
            items.append(PublicArticle(title=title, url=href, date_text=date_text))

    return items


def load_existing_titles(csv_path: str) -> Set[str]:
    """
    Load titles already saved as missing to avoid duplicates.
    """
    if not os.path.exists(csv_path):
        return set()

    seen: Set[str] = set()
    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw = (row.get("title") or "").strip()
            if raw:
                seen.add(normalize_title(raw).lower())
    return seen


def append_missing(csv_path: str, row: Tuple[str, str, str, str, str]) -> None:
    """
    Append a missing article record to the output CSV.
    Apply formatting tweaks:
    - Add trailing space after article URL
    - Add trailing space after page URL
    """
    file_exists = os.path.exists(csv_path)

    row_list = list(row)
    # Add spaces in URL fields for cleaner CSV readability
    if row_list[1]:
        row_list[1] = row_list[1] + " "
    if row_list[2]:
        row_list[2] = row_list[2] + " "

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["title", "public_url", "public_list_url", "public_date", "checked_at"])
        writer.writerow(row_list)


def build_cms_content_url(cms_base_url: str) -> str:
    """
    Construct the CMS admin content page URL.
    """
    base = cms_base_url if cms_base_url.endswith("/") else cms_base_url + "/"
    return urljoin(base, "en/admin/content")


async def cms_title_exists(page, cms_base_url: str, title: str) -> bool:
    """
    Query the CMS content list to check if a given article title exists.
    """
    clean = normalize_title(title)
    params = {
        "title": clean,
        "type": "All",
        "status": "All",
        "langcode": "All",
    }
    url = build_cms_content_url(cms_base_url) + "?" + urlencode(params, doseq=False)

    print(f"Checking CMS for: {clean}")
    await page.goto(url, wait_until="networkidle")
    await page.wait_for_timeout(300)

    no_results = page.locator("text=No content available").first
    if await no_results.count() > 0:
        print("    Not found in CMS")
        return False

    table = page.locator("table.views-table")
    view_content = page.locator(".view-content")
    if await table.count() == 0 and await view_content.count() == 0:
        raise RuntimeError("CMS results not visible; session may be invalid")

    links = page.locator("table.views-table tbody td.views-field-title a")
    count = await links.count()
    if count == 0:
        print("    No title links in CMS table")
        return False

    wanted = clean.lower()
    for i in range(count):
        t = normalize_title(await links.nth(i).inner_text()).lower()
        if t == wanted:
            print("    Found")
            return True

    print("    Not found")
    return False


async def init_auth_state(cms_base_url: str, storage_state_path: str) -> None:
    """
    Opens a browser window for user login, then saves session cookies to JSON.
    """
    print("Launching browser for manual login...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        url = build_cms_content_url(cms_base_url)
        print(f"Opening: {url}")
        await page.goto(url, wait_until="domcontentloaded")

        input("Log in, then press Enter to continue...")
        await context.storage_state(path=storage_state_path)
        await browser.close()
    print(f"Saved auth session -> {storage_state_path}")


def timestamped_csv_name(path: str) -> str:
    """
    Add a timestamp to CSV name so each run generates a fresh file.
    """
    base, ext = os.path.splitext(path)
    ext = ext or ".csv"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{base}_{ts}{ext}"


async def run_sync(
    cms_base_url: str,
    public_list_url: str,
    start_page: int,
    end_page: int,
    storage_state_path: str,
    out_csv: str,
    limit_per_page: Optional[int] = None,
) -> None:
    """
    Main sync workflow:
    - Fetch public pages
    - Normalize titles
    - Check CMS for each title
    - Save missing ones
    """
    print("Sync started")
    print(f"Results file: {out_csv}")

    already_missing = load_existing_titles(out_csv)

    total_scanned = 0
    total_missing = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=storage_state_path)
        page = await context.new_page()

        for page_num in range(start_page, end_page + 1):
            list_url = public_page_url(public_list_url, page_num)
            public_items = await fetch_public_articles(public_list_url, page_num)

            if limit_per_page is not None:
                public_items = public_items[:limit_per_page]

            total_scanned += len(public_items)

            for item in public_items:
                clean = normalize_title(item.title)
                print(f"Processing: {clean}")

                if clean.lower() in already_missing:
                    continue

                exists = await cms_title_exists(page, cms_base_url, clean)
                if not exists:
                    checked_at = datetime.now().isoformat(timespec="seconds")
                    append_missing(
                        out_csv,
                        (
                            clean,
                            item.url,
                            list_url,
                            item.date_text,
                            checked_at,
                        ),
                    )
                    already_missing.add(clean.lower())
                    total_missing += 1

        await context.close()
        await browser.close()

    print("Sync complete")
    print(f"Total public articles scanned: {total_scanned}")
    print(f"Total missing (not found in CMS): {total_missing}")


def main() -> None:
    """
    CLI entry:
    --init-auth : open browser for manual login
    otherwise : run sync job
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--init-auth", action="store_true")
    parser.add_argument("--cms-base-url", default=os.getenv("CMS_BASE_URL", "").strip())
    parser.add_argument(
        "--public-list-url",
        default=os.getenv("PUBLIC_LIST_URL", "https://mfa.gov.lk/en/category/media-releases/").strip(),
    )
    parser.add_argument("--storage-state", default="cms_storage_state.json")
    parser.add_argument("--out", default="missing_articles.csv")
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--end-page", type=int, default=3)
    parser.add_argument("--limit-per-page", type=int, default=None)

    args = parser.parse_args()

    if not args.cms_base_url:
        raise SystemExit("Missing CMS_BASE_URL")

    if args.init_auth:
        asyncio.run(init_auth_state(args.cms_base_url, args.storage_state))
        return

    if not os.path.exists(args.storage_state):
        raise SystemExit("No auth state stored, run --init-auth first")

    out_csv = timestamped_csv_name(args.out)

    asyncio.run(
        run_sync(
            cms_base_url=args.cms_base_url,
            public_list_url=args.public_list_url,
            start_page=args.start_page,
            end_page=args.end_page,
            storage_state_path=args.storage_state,
            out_csv=out_csv,
            limit_per_page=args.limit_per_page,
        )
    )


if __name__ == "__main__":
    main()
