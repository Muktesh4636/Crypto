"""Persist RSS-normalized dicts into the database."""

from __future__ import annotations

from typing import Any

from ..models import NewsArticle


def upsert_news_articles(items: list[dict[str, Any]]) -> tuple[int, int]:
    """
    Insert or update by URL. Returns (created_count, updated_count).
    """
    created_n = 0
    updated_n = 0
    for raw in items:
        link = (raw.get("link") or "").strip()
        title = (raw.get("title") or "").strip()
        if not link or not title:
            continue
        pub = raw.get("published_at")
        defaults = {
            "title": title[:512],
            "summary": (raw.get("summary") or "")[:15000],
            "source_feed": ((raw.get("source_feed") or "")[:255]),
            "topic_slug": (raw.get("topic_slug") or "")[:64],
            "published_at": pub,
        }
        obj, was_created = NewsArticle.objects.update_or_create(
            url=link[:10000],
            defaults=defaults,
        )
        if was_created:
            created_n += 1
        else:
            updated_n += 1
    return created_n, updated_n
