"""
The Guardian Content API lets you paginate archived articles within a date range.

This does **NOT** scrape "the entire world's news" — it retrieves articles that match
search queries inside The Guardian corpus. Typical free-tier limits apply; a full multi-year
pull for many topics should be spaced over days (`--sleep`), or negotiated with Guardian.

Refs: https://open-platform.theguardian.com/documentation/
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

GUARDIAN_SEARCH_URL = "https://content.guardianapis.com/search"

# Covers user themes: geopolitics, monetary policy / Fed, ETFs & flows, macro prices,
# global politics, elections, trade wars, and crypto regulation.
DEFAULT_TOPICS: tuple[tuple[str, str], ...] = (
    # ── Macro / monetary ─────────────────────────────────────────────────
    ("fed_rates", '"Federal Reserve" OR FOMC OR "interest rates" OR monetary policy OR Powell'),
    (
        "etf_institutional_flows",
        "ETF flows OR ETF inflows OR ETF outflows OR institutional flows OR passive investing",
    ),
    ("inflation", "inflation OR CPI OR consumer prices OR deflation OR price pressures"),
    ("oil_energy", "oil price OR crude oil OR OPEC OR WTI OR Brent OR energy prices"),
    ("macro", "GDP OR recession OR PMI OR macroeconomy OR unemployment OR fiscal policy"),
    # ── Geopolitics / conflicts / sanctions ──────────────────────────────
    ("geopolitics", "geopolitics OR geopolitical OR war OR conflict OR sanctions OR NATO"),
    ("sanctions_trade", "sanctions OR trade war OR tariffs OR export controls OR economic warfare"),
    ("russia_ukraine", "Russia OR Ukraine OR Zelensky OR Putin OR Kremlin OR NATO OR Kyiv"),
    ("middle_east", "Israel OR Gaza OR Palestine OR Hamas OR Hezbollah OR Iran OR Lebanon OR Syria"),
    ("us_china", '"US-China" OR "China-US" OR Taiwan OR "South China Sea" OR Huawei OR decoupling'),
    # ── US politics ───────────────────────────────────────────────────────
    ("us_politics", 'US election OR Congress OR Senate OR White House OR Trump OR Biden OR Harris'),
    ("elections", "election OR referendum OR vote OR ballot OR polling OR democracy"),
    # ── Global politics ───────────────────────────────────────────────────
    ("global_politics", "political crisis OR coup OR protest OR government collapse OR regime"),
    ("eu_politics", "European Union OR EU OR Eurozone OR ECB OR Macron OR Brussels OR NATO"),
    ("asia_politics", "China OR India OR Japan OR South Korea OR ASEAN OR Modi OR Xi Jinping"),
    ("latam_politics", "Latin America OR Brazil OR Argentina OR Mexico OR Venezuela OR Colombia"),
    # ── Crypto regulation / policy ────────────────────────────────────────
    (
        "crypto_regulation",
        "cryptocurrency regulation OR Bitcoin law OR crypto ban OR SEC crypto OR "
        "stablecoin regulation OR CBDC OR digital asset policy OR MiCA",
    ),
)


@dataclass
class GuardianPageResult:
    items: list[dict[str, Any]]
    total_pages: int
    current_page: int


def _parse_gu_time(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def fetch_guardian_page(
    *,
    api_key: str,
    query: str,
    date_from: date,
    date_to: date,
    page: int = 1,
    page_size: int = 50,
    timeout: float = 30.0,
) -> GuardianPageResult:
    params = {
        "q": query,
        "from-date": date_from.isoformat(),
        "to-date": date_to.isoformat(),
        "page": page,
        "page-size": min(max(page_size, 1), 50),
        "show-fields": "trailText,headline",
        "order-by": "newest",
        "api-key": api_key,
    }
    r = requests.get(GUARDIAN_SEARCH_URL, params=params, timeout=timeout)
    r.raise_for_status()
    payload = r.json()
    resp = payload.get("response") or {}
    if resp.get("status") == "error":
        raise RuntimeError(payload.get("message") or "Guardian API error")
    results = resp.get("results") or []
    items: list[dict[str, Any]] = []
    for row in results:
        fields = row.get("fields") or {}
        trail = (fields.get("trailText") or "").strip()
        if len(trail) > 2000:
            trail = trail[:1997] + "…"
        items.append(
            {
                "title": (row.get("webTitle") or "").strip(),
                "link": (row.get("webUrl") or "").strip(),
                "published_at": _parse_gu_time(row.get("webPublicationDate")),
                "summary": trail,
                "source_feed": f"theguardian.com/{(row.get('sectionName') or 'unknown')[:80]}",
            }
        )
    return GuardianPageResult(
        items=items,
        total_pages=int(resp.get("pages") or 1),
        current_page=int(resp.get("currentPage") or page),
    )


def iter_guardian_topic_months(
    *,
    api_key: str,
    topic_slug: str,
    query: str,
    date_from: date,
    date_to: date,
    max_pages_per_month: int = 40,
    sleep_sec: float = 1.0,
) -> Any:
    """
    Yield normalized news dicts (ready for `upsert_news_articles`) month by month.
    """
    cursor = date_from.replace(day=1)
    end_month = date_to.replace(day=1)
    while cursor <= end_month:
        next_month = (cursor + timedelta(days=32)).replace(day=1)
        win_start = max(cursor, date_from)
        win_end = min(next_month - timedelta(days=1), date_to)
        page = 1
        while page <= max_pages_per_month:
            pr = fetch_guardian_page(
                api_key=api_key,
                query=query,
                date_from=win_start,
                date_to=win_end,
                page=page,
            )
            for it in pr.items:
                it["topic_slug"] = topic_slug
                yield it
            if page >= pr.total_pages or not pr.items:
                break
            page += 1
            if sleep_sec > 0:
                time.sleep(sleep_sec)
        cursor = next_month
        if sleep_sec > 0:
            time.sleep(sleep_sec)
