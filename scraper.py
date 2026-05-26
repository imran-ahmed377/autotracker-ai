from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API_BASE = "https://vpic.nhtsa.dot.gov/api/vehicles"
DEFAULT_MAX_MAKES = 15
DEFAULT_YEAR_COUNT = 3
OUTPUT_DIR = Path("output")
EXCEL_PATH = OUTPUT_DIR / "cars.xlsx"
CSV_PATH = OUTPUT_DIR / "cars.csv"


@dataclass(frozen=True)
class CarRecord:
    listing_id: str
    title: str
    make: Optional[str]
    model: Optional[str]
    year: Optional[int]
    transmission: Optional[str]
    fuel: Optional[str]
    mileage: Optional[str]
    price: Optional[str]
    location: Optional[str]
    source_url: str
    scraped_at: str


def build_session() -> requests.Session:
    retry = Retry(
        total=4,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods={"GET"},
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)

    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(
        {"User-Agent": "autotracker-ai/1.0 (+github-actions)"})
    return session


def fetch_json(session: requests.Session, url: str, timeout_seconds: int = 30) -> list[dict[str, Any]]:
    response = session.get(url, timeout=timeout_seconds)
    response.raise_for_status()

    payload = response.json()
    if not isinstance(payload, dict) or "Results" not in payload:
        raise ValueError(f"Unexpected API response shape from: {url}")

    results = payload["Results"]
    if not isinstance(results, list):
        raise ValueError(f"Unexpected results value from: {url}")

    return results


def fetch_makes(session: requests.Session, max_makes: int) -> list[dict[str, Any]]:
    # NHTSA makes endpoint is US-focused; for Canadian scope we keep this minimal
    url = f"{API_BASE}/GetMakesForVehicleType/car?format=json"
    rows = fetch_json(session, url)

    filtered = [row for row in rows if row.get("MakeName")]
    filtered.sort(key=lambda row: str(row.get("MakeName", "")))
    return filtered[:max_makes]


def fetch_transport_canada_datasets(session: requests.Session, max_results: int = 10) -> list[CarRecord]:
    """Query Open.Canada CKAN API for Transport Canada datasets as a safe public data source.

    This is intentionally lightweight: we convert dataset metadata into CarRecord-like rows
    to demonstrate a Transport Canada source without scraping private marketplaces.
    """
    api = "https://open.canada.ca/data/en/api/3/action/package_search"
    params = {"q": "transport canada vehicle", "rows": max_results}
    resp = session.get(api, params=params, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    results = payload.get("result", {}).get(
        "results", []) if isinstance(payload, dict) else []

    now_iso = datetime.now(timezone.utc).isoformat()
    records: list[CarRecord] = []
    for item in results:
        title = item.get("title") or item.get(
            "name") or "Transport Canada dataset"
        listing_id = f"tc-{item.get('id', title)[:24]}"
        records.append(
            CarRecord(
                listing_id=listing_id,
                title=title,
                make=None,
                model=None,
                year=None,
                transmission=None,
                fuel=None,
                mileage=None,
                price=None,
                location="Canada",
                source_url=item.get("url", "https://open.canada.ca"),
                scraped_at=now_iso,
            )
        )

    return records


def fetch_models_for_make_year(session: requests.Session, make_name: str, year: int) -> list[dict[str, Any]]:
    url = (
        f"{API_BASE}/GetModelsForMakeYear/make/{make_name}"
        f"/modelyear/{year}/vehicletype/car?format=json"
    )
    return fetch_json(session, url)


def build_records(session: requests.Session, max_makes: int, year_count: int) -> list[CarRecord]:
    # Compose records from two Canadian sources:
    # 1) Transport Canada (public datasets)
    # 2) AutoTrader.ca (marketplace via Playwright)
    records: list[CarRecord] = []

    # 1) Transport Canada metadata
    try:
        tc = fetch_transport_canada_datasets(session, max_results=10)
        records.extend(tc)
    except Exception:
        # don't fail entire run for Transport Canada metadata issues
        pass

    # 2) AutoTrader.ca — live marketplace requires Playwright; we invoke it conditionally
    try:
        at_records = fetch_autotrader_listings_playwright(max_pages=1)
        records.extend(at_records)
    except Exception:
        # AutoTrader scraping may fail due to environment; surface nothing and continue
        pass

    return records


def fetch_autotrader_listings_playwright(max_pages: int = 1) -> list[CarRecord]:
    """Use Playwright to scrape AutoTrader.ca search results for Canada.

    This implementation is intentionally conservative: it scrapes a single results
    page and extracts a few fields to avoid hammering the site. It requires the
    `playwright` package and installed browsers (the workflow installs these).
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError(
            "Playwright is not available in this environment") from exc

    records: list[CarRecord] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    search_url = "https://www.autotrader.ca/cars/?rcp=20&rcs=0&loc=Canada"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.goto(search_url, timeout=60000)
        page.wait_for_timeout(3000)

        # Generic selector for result cards; try a few possibilities
        selectors = [
            "a[data-testid='result-listing']",
            "a.result-item",
            "a.listingCard",
        ]

        anchors = []
        for sel in selectors:
            anchors = page.query_selector_all(sel)
            if anchors:
                break

        for a in anchors[:50]:
            try:
                href = a.get_attribute("href") or ""
                source_url = page.url.rstrip(
                    "/") + href if href.startswith("/") else href
                title = a.inner_text().strip()[:200]

                # Price and location heuristics
                price = None
                loc = None
                try:
                    price_el = a.query_selector(
                        ".price") or a.query_selector("span[data-qa='price']")
                    if price_el:
                        price = price_el.inner_text().strip()
                except Exception:
                    price = None

                try:
                    loc_el = a.query_selector(".location") or a.query_selector(
                        "span[data-qa='sellerLocation']")
                    if loc_el:
                        loc = loc_el.inner_text().strip()
                except Exception:
                    loc = None

                listing_id = f"at-{hash(source_url) & 0xFFFFFFFF:08x}"
                # Best-effort parse for make/model/year from title
                make = None
                model = None
                year = None
                parts = title.split()
                if parts and parts[0].isdigit():
                    try:
                        year = int(parts[0])
                        if len(parts) >= 3:
                            make = parts[1]
                            model = parts[2]
                    except Exception:
                        year = None

                records.append(
                    CarRecord(
                        listing_id=listing_id,
                        title=title,
                        make=make,
                        model=model,
                        year=year,
                        transmission=None,
                        fuel=None,
                        mileage=None,
                        price=price,
                        location=loc or "Canada",
                        source_url=source_url,
                        scraped_at=now_iso,
                    )
                )
            except Exception:
                continue

        context.close()
        browser.close()

    return records


def to_dataframe(records: list[CarRecord]) -> pd.DataFrame:
    rows = [record.__dict__ for record in records]
    df = pd.DataFrame(rows)

    column_order = [
        "listing_id",
        "title",
        "make",
        "model",
        "year",
        "transmission",
        "fuel",
        "mileage",
        "price",
        "location",
        "source_url",
        "scraped_at",
    ]

    for column in column_order:
        if column not in df.columns:
            df[column] = None

    df = df[column_order]
    df = df.drop_duplicates(subset=["listing_id"]).sort_values(
        by=["year", "make", "model"], ascending=[False, True, True]
    )
    return df


def write_outputs(df: pd.DataFrame) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_excel(EXCEL_PATH, index=False)
    df.to_csv(CSV_PATH, index=False)


def main() -> int:
    max_makes = int(os.getenv("MAX_MAKES", str(DEFAULT_MAX_MAKES)))
    year_count = int(os.getenv("YEAR_COUNT", str(DEFAULT_YEAR_COUNT)))

    if max_makes < 1 or year_count < 1:
        print("MAX_MAKES and YEAR_COUNT must be positive integers.")
        return 2

    session = build_session()
    try:
        records = build_records(
            session, max_makes=max_makes, year_count=year_count)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        print(
            f"HTTP error while calling NHTSA API: status={status}, detail={exc}")
        return 1
    except requests.RequestException as exc:
        print(f"Network error while calling NHTSA API: {exc}")
        return 1
    except Exception as exc:
        print(f"Unexpected failure while scraping data: {exc}")
        return 1

    if not records:
        print(
            "No records were returned from the API; aborting to avoid empty output commit.")
        return 1

    df = to_dataframe(records)
    if df.empty:
        print("Dataframe is empty after normalization; aborting.")
        return 1

    write_outputs(df)
    print(f"Wrote {len(df)} rows to {EXCEL_PATH} and {CSV_PATH}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
