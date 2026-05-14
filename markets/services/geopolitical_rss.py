"""
Geopolitical and political RSS feeds — all publicly available, no API key required.

Topics tag each article so they can be filtered/analysed later.
Each feed is mapped to one or more topic_slug values aligned with the
NewsArticle.topic_slug field.

Crypto ↔ geopolitics intersections covered:
  • sanctions / trade-war news (US-China, Russia, Iran) affect BTC flows & stablecoin demand
  • election outcomes shift crypto-regulation posture
  • central-bank / monetary policy decisions drive BTC risk-on behaviour
  • regional conflicts affect mining (energy, hashrate geography)
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import feedparser
import requests

FETCH_TIMEOUT = 15.0
_MAX_PER_FEED = 50

# ──────────────────────────────────────────────
# Feed catalog
# Each entry: (url, topic_slug, human_label)
# ──────────────────────────────────────────────
GEOPOLITICAL_FEEDS: tuple[tuple[str, str, str], ...] = (
    # ── Global / world affairs ──────────────────────────────────────────
    ("https://feeds.bbci.co.uk/news/world/rss.xml",               "global_politics",   "BBC World"),
    ("https://feeds.bbci.co.uk/news/politics/rss.xml",            "us_politics",       "BBC Politics"),
    ("https://www.aljazeera.com/xml/rss/all.xml",                  "global_politics",   "Al Jazeera"),
    ("https://rss.dw.com/rdf/rss-en-world",                        "global_politics",   "DW World"),
    ("https://www.france24.com/en/rss",                            "global_politics",   "France24"),
    ("https://feeds.skynews.com/feeds/rss/world.xml",              "global_politics",   "Sky News World"),
    ("https://www.theguardian.com/world/rss",                      "global_politics",   "Guardian World"),
    ("https://www.theguardian.com/politics/rss",                   "uk_politics",       "Guardian Politics"),
    # ── US politics ─────────────────────────────────────────────────────
    ("https://thehill.com/rss/syndicator/19110/",                  "us_politics",       "The Hill"),
    ("https://feeds.npr.org/1014/rss.xml",                         "us_politics",       "NPR Politics"),
    ("https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",  "us_politics",       "NYT Politics"),
    ("https://abcnews.go.com/abcnews/politicsheadlines",           "us_politics",       "ABC Politics"),
    ("https://feeds.foxnews.com/foxnews/politics",                 "us_politics",       "Fox Politics"),
    ("https://www.cbsnews.com/latest/rss/politics",                "us_politics",       "CBS Politics"),
    # ── Geopolitics / conflicts / sanctions ─────────────────────────────
    ("https://foreignpolicy.com/feed/",                            "geopolitics",       "Foreign Policy"),
    ("https://thediplomat.com/feed/",                              "asia_politics",     "The Diplomat"),
    ("https://www.middleeasteye.net/rss",                          "middle_east",       "Middle East Eye"),
    ("https://asiatimes.com/feed/",                                "asia_politics",     "Asia Times"),
    ("https://www.scmp.com/rss/91/feed",                           "asia_politics",     "SCMP World"),
    ("https://www.scmp.com/rss/2/feed",                            "asia_politics",     "SCMP China"),
    ("https://www.bellingcat.com/feed/",                           "geopolitics",       "Bellingcat"),
    ("https://www.crisisgroup.org/rss.xml",                        "geopolitics",       "Crisis Group"),
    # ── Europe / EU ──────────────────────────────────────────────────────
    ("https://www.theguardian.com/world/europe-news/rss",          "eu_politics",       "Guardian Europe"),
    ("https://www.euractiv.com/sections/politics/feed/",           "eu_politics",       "Euractiv Politics"),
    ("https://www.politico.eu/feed/",                              "eu_politics",       "Politico EU"),
    ("https://feeds.bbci.co.uk/news/world/europe/rss.xml",         "eu_politics",       "BBC Europe"),
    # ── Asia / Indo-Pacific ──────────────────────────────────────────────
    ("https://feeds.bbci.co.uk/news/world/asia/rss.xml",           "asia_politics",     "BBC Asia"),
    ("https://www.theguardian.com/world/asia/rss",                 "asia_politics",     "Guardian Asia"),
    # ── Latin America ─────────────────────────────────────────────────────
    ("https://feeds.bbci.co.uk/news/world/latin_america/rss.xml",  "latam_politics",    "BBC LatAm"),
    ("https://www.theguardian.com/world/americas/rss",             "latam_politics",    "Guardian Americas"),
    # ── Africa ───────────────────────────────────────────────────────────
    ("https://feeds.bbci.co.uk/news/world/africa/rss.xml",         "africa_politics",   "BBC Africa"),
    # ── Elections worldwide ───────────────────────────────────────────────
    ("https://www.theguardian.com/world/rss",                      "elections",         "Guardian World+Elections"),
    ("https://feeds.bbci.co.uk/news/world/rss.xml",                "elections",         "BBC World+Elections"),
    # ── Crypto regulation / policy nexus ────────────────────────────────
    ("https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml",
                                                                    "crypto_regulation", "CoinDesk"),
    ("https://cointelegraph.com/rss",                              "crypto_regulation", "CoinTelegraph"),
    ("https://decrypt.co/feed",                                    "crypto_regulation", "Decrypt"),
    ("https://www.theguardian.com/technology/cryptocurrencies/rss","crypto_regulation", "Guardian Crypto"),
    ("https://cryptonews.com/news/feed/",                          "crypto_regulation", "CryptoNews"),
    # ── Trade / sanctions / economic warfare ─────────────────────────────
    ("https://www.theguardian.com/business/economics/rss",         "sanctions_trade",   "Guardian Economics"),
    ("https://feeds.bbci.co.uk/news/business/rss.xml",             "sanctions_trade",   "BBC Business"),
    ("https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml",   "sanctions_trade",   "NYT Economy"),
)


# ──────────────────────────────────────────────
# Internal helpers (same pattern as news_rss.py)
# ──────────────────────────────────────────────

def _feed_label(url: str, fallback_label: str) -> str:
    try:
        host = urlparse(url).netloc or fallback_label
        return host.replace("www.", "")
    except Exception:
        return fallback_label


def _published_utc(entry: Any) -> datetime | None:
    t = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not t:
        return None
    try:
        return datetime(
            t.tm_year, t.tm_mon, t.tm_mday,
            t.tm_hour, t.tm_min, t.tm_sec,
            tzinfo=timezone.utc,
        )
    except (ValueError, TypeError):
        return None


def _normalize_entry(entry: Any, feed_url: str, topic_slug: str, source_label: str) -> dict[str, Any]:
    title = (getattr(entry, "title", "") or "").strip()
    link = (getattr(entry, "link", "") or "").strip()
    summary = ""
    if getattr(entry, "summary", None):
        summary = entry.summary
    elif getattr(entry, "description", None):
        summary = entry.description
    if summary and len(summary) > 2000:
        summary = summary[:1997] + "…"
    return {
        "title": title,
        "link": link,
        "published_at": _published_utc(entry),
        "summary": summary.strip(),
        "source_feed": source_label,
        "topic_slug": topic_slug,
    }


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────

def fetch_geopolitical_news(
    feeds: tuple[tuple[str, str, str], ...] | None = None,
    timeout: float = FETCH_TIMEOUT,
    max_per_feed: int = _MAX_PER_FEED,
    only_topics: set[str] | None = None,
    sleep_between: float = 0.3,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Fetch all configured geopolitical/political RSS feeds.

    Returns (items, per_feed_status).
    Each item has keys: title, link, published_at, summary, source_feed, topic_slug.

    Args:
        feeds:          Override the default feed catalog.
        timeout:        HTTP timeout per request.
        max_per_feed:   Max articles kept per feed.
        only_topics:    If provided, skip feeds whose topic_slug is not in this set.
        sleep_between:  Seconds between HTTP calls (be polite).
    """
    catalog = feeds or GEOPOLITICAL_FEEDS
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []

    for url, topic_slug, label in catalog:
        if only_topics and topic_slug not in only_topics:
            continue
        st: dict[str, Any] = {
            "url": url,
            "topic_slug": topic_slug,
            "label": label,
            "ok": False,
            "error": None,
            "count": 0,
        }
        try:
            r = requests.get(
                url,
                timeout=timeout,
                headers={"User-Agent": "crypto_claude-geopolitics-aggregator/1.0"},
            )
            r.raise_for_status()
            parsed = feedparser.parse(r.content)
            n = 0
            for entry in (getattr(parsed, "entries", []) or [])[:max_per_feed]:
                item = _normalize_entry(entry, url, topic_slug, label)
                if not item["title"] or not item["link"]:
                    continue
                if item["link"] in seen:
                    continue
                seen.add(item["link"])
                merged.append(item)
                n += 1
            st["ok"] = True
            st["count"] = n
        except Exception as exc:
            st["error"] = str(exc)
        statuses.append(st)
        if sleep_between > 0:
            time.sleep(sleep_between)

    return merged, statuses


TOPIC_SLUGS: tuple[str, ...] = tuple(
    dict.fromkeys(slug for _, slug, _ in GEOPOLITICAL_FEEDS)
)
