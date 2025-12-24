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


@dataclass(frozen=True)
class PublicArticle:
    title: str
    url: str
    date_text: str


_whitespace_re = re.compile(r"\s+")


def normalize_title(raw: str) -> str:
    s = html.unescape(raw)
    s = s.replace("\u00a0", " ")
    s = _whitespace_re.sub(" ", s).strip()
    return s


def public_page_url(base_list_url: str, page_num: int) -> str:
    if page_num <= 1:
        return base_list_url if base_list_url.endswith("/") else base_list_url + "/"
    base = base_list_url if base_list_url.endswith("/") else base_list_url + "/"
    return urljoin(base, f"page/{page_num}/")


async def fetch_public_articles(base_list_url: str, page_num: int, timeout_s: float = 30.0) -> List[PublicArticle]:
    url = public_page_url(base_list_url, page_num)
    async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    main = soup.select_one("section#main")
    if not main:
        return []

    items: List[PublicArticle] = []
    for a in main.select("#post-area article h2.entry-title a"):
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
    if not os.path.exists(csv_path):
        return set()

    seen: Set[str] = set()
    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            t = (row.get("title") or "").strip()
            if t:
                seen.add(t.lower())
    return seen


def append_missing(csv_path: str, row: Tuple[str, str, str, str]) -> None:
    file_exists = os.path.exists(csv_path)
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["title", "public_url", "public_date", "checked_at"])
        writer.writerow(row)


def build_cms_content_url(cms_base_url: str) -> str:
    base = cms_base_url if cms_base_url.endswith("/") else cms_base_url + "/"
    return urljoin(base, "en/admin/content")


async def cms_title_exists(page, cms_base_url: str, title: str) -> bool:
    params = {
        "title": title,
        "type": "All",
        "status": "All",
        "langcode": "All",
    }
    url = build_cms_content_url(cms_base_url) + "?" + urlencode(params, doseq=False)

    await page.goto(url, wait_until="networkidle")
    await page.wait_for_timeout(300)

    no_results = page.locator("text=No content available").first
    if await no_results.count() > 0:
        return False

    table = page.locator("table.views-table")
    view_content = page.locator(".view-content")
    if await table.count() == 0 and await view_content.count() == 0:
        html_text = await page.content()
        if "view-content" not in html_text and "views-table" not in html_text:
            raise RuntimeError("CMS results container not found; login/session may be invalid.")

    links = page.locator("table.views-table tbody td.views-field-title a")
    count = await links.count()
    if count == 0:
        return False

    wanted = normalize_title(title).lower()
    for i in range(count):
        txt = normalize_title(await links.nth(i).inner_text()).lower()
        if txt == wanted:
            return True

    return False


async def init_auth_state(cms_base_url: str, storage_state_path: str) -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        start_url = build_cms_content_url(cms_base_url)
        await page.goto(start_url, wait_until="domcontentloaded")

        input("Finish logging into the CMS in the opened browser, then press Enter here...")
        await context.storage_state(path=storage_state_path)
        await browser.close()


def timestamped_csv_name(path: str) -> str:
    base, ext = os.path.splitext(path)
    ext = ext if ext else ".csv"
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
    already_missing = load_existing_titles(out_csv)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=storage_state_path)
        page = await context.new_page()

        for page_num in range(start_page, end_page + 1):
            public_items = await fetch_public_articles(public_list_url, page_num)
            if limit_per_page is not None:
                public_items = public_items[:limit_per_page]

            if not public_items:
                continue

            for item in public_items:
                if item.title.lower() in already_missing:
                    continue

                exists = await cms_title_exists(page, cms_base_url, item.title)
                if not exists:
                    checked_at = datetime.now().isoformat(timespec="seconds")
                    append_missing(out_csv, (item.title, item.url, item.date_text, checked_at))
                    already_missing.add(item.title.lower())

        await context.close()
        await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--init-auth", action="store_true", help="Open browser to login and save session state")
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
        raise SystemExit("Missing --cms-base-url (or set CMS_BASE_URL env var).")

    if args.init_auth:
        asyncio.run(init_auth_state(args.cms_base_url, args.storage_state))
        return

    if not os.path.exists(args.storage_state):
        raise SystemExit(f"Storage state not found: {args.storage_state}. Run with --init-auth first.")

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
