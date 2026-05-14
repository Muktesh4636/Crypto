from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import feedparser
import requests

# Public RSS only — a tiny slice of “world” headlines. Licensed news APIs are needed
# for comprehensive, legal, reliable global coverage.
DEFAULT_RSS_FEEDS: tuple[str, ...] = (
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://www.theguardian.com/world/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml",
)

_CACHE_LOCK = threading.Lock()
_CACHE_ITEMS: list[dict[str, Any]] | None = None
_CACHE_FEEDS_STATUS: list[dict[str, Any]] | None = None
_CACHE_EXPIRES_MONO: float = 0.0
_CACHE_TTL_SEC = 300.0
FETCH_TIMEOUT = 12.0
_MAX_PER_FEED = 40
_MAX_TOTAL = 120


def _feed_label(url: str) -> str:
    try:
        host = urlparse(url).netloc or url
        return host.replace("www.", "")
    except Exception:
        return url


def _published_utc(entry: Any) -> datetime | None:
    t = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not t:
        return None
    try:
        return datetime(
            t.tm_year,
            t.tm_mon,
            t.tm_mday,
            t.tm_hour,
            t.tm_min,
            t.tm_sec,
            tzinfo=timezone.utc,
        )
    except (ValueError, TypeError):
        return None


def _normalize_entry(entry: Any, feed_url: str) -> dict[str, Any]:
    title = getattr(entry, "title", "") or ""
    link = getattr(entry, "link", "") or ""
    published = getattr(entry, "published", None) or getattr(entry, "updated", None) or ""
    summary = ""
    if getattr(entry, "summary", None):
        summary = entry.summary
    elif getattr(entry, "description", None):
        summary = entry.description
    if summary and len(summary) > 400:
        summary = summary[:397] + "…"
    pub_dt = _published_utc(entry)
    return {
        "title": title.strip(),
        "link": link.strip(),
        "published": str(published).strip(),
        "published_at": pub_dt,
        "summary": summary.strip(),
        "source_feed": _feed_label(feed_url),
    }


def fetch_world_news_sample(
    feeds: tuple[str, ...] | None = None,
    timeout: float = FETCH_TIMEOUT,
    *,
    max_per_feed: int | None = None,
    max_total: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Pull and merge several RSS feeds; dedupe by link. Returns (items, per_feed_status).
    """
    per_cap = max_per_feed if max_per_feed is not None else _MAX_PER_FEED
    total_cap = max_total if max_total is not None else _MAX_TOTAL
    per_cap = max(1, min(per_cap, 200))
    total_cap = max(1, min(total_cap, 2000))

    feed_urls = feeds or DEFAULT_RSS_FEEDS
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []

    for url in feed_urls:
        st: dict[str, Any] = {"url": url, "ok": False, "error": None, "count": 0}
        try:
            # Some servers block non-browser UAs; requests pre-fetch often works better.
            r = requests.get(
                url,
                timeout=timeout,
                headers={"User-Agent": "crypto_claude-news-aggregator/1.0"},
            )
            r.raise_for_status()
            parsed = feedparser.parse(r.content)
            n = 0
            for entry in getattr(parsed, "entries", [])[:per_cap]:
                item = _normalize_entry(entry, url)
                if not item["title"] or not item["link"]:
                    continue
                if item["link"] in seen:
                    continue
                seen.add(item["link"])
                merged.append(item)
                n += 1
                if len(merged) >= total_cap:
                    break
            st["ok"] = True
            st["count"] = n
        except Exception as exc:
            st["error"] = str(exc)
        statuses.append(st)
        if len(merged) >= total_cap:
            break

    return merged, statuses


def get_cached_world_news_sample() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    global _CACHE_ITEMS, _CACHE_FEEDS_STATUS, _CACHE_EXPIRES_MONO
    now = time.monotonic()
    with _CACHE_LOCK:
        if _CACHE_ITEMS is not None and now < _CACHE_EXPIRES_MONO:
            return _CACHE_ITEMS, _CACHE_FEEDS_STATUS or []

    items, statuses = fetch_world_news_sample()
    with _CACHE_LOCK:
        _CACHE_ITEMS = items
        _CACHE_FEEDS_STATUS = statuses
        _CACHE_EXPIRES_MONO = time.monotonic() + _CACHE_TTL_SEC
    return items, statuses
