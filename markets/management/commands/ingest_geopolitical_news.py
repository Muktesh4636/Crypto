"""
Management command: ingest_geopolitical_news

Pulls geopolitical and political RSS feeds (no API key required) and optionally
runs a Guardian API historical backfill for the same political topics.

Usage examples:

    # Pull all live RSS feeds right now
    python manage.py ingest_geopolitical_news

    # Pull only specific topic buckets from RSS
    python manage.py ingest_geopolitical_news --only geopolitics,us_politics,crypto_regulation

    # Also backfill 4 years of Guardian history for new political topics
    python manage.py ingest_geopolitical_news --guardian-history --days 1461

    # Backfill Guardian only (skip RSS)
    python manage.py ingest_geopolitical_news --skip-rss --guardian-history --days 730

Available topic slugs (RSS):
    global_politics, uk_politics, us_politics, eu_politics, asia_politics,
    latam_politics, africa_politics, middle_east, geopolitics, elections,
    crypto_regulation, sanctions_trade

Available topic slugs (Guardian history):
    geopolitics, sanctions_trade, russia_ukraine, middle_east, us_china,
    us_politics, elections, global_politics, eu_politics, asia_politics,
    latam_politics, crypto_regulation
    (also all original topics: fed_rates, inflation, oil_energy, macro, etf_institutional_flows)
"""

from __future__ import annotations

from datetime import date, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand

from markets.services.geopolitical_rss import GEOPOLITICAL_FEEDS, TOPIC_SLUGS, fetch_geopolitical_news
from markets.services.guardian_news import DEFAULT_TOPICS, iter_guardian_topic_months
from markets.services.news_store import upsert_news_articles

# Guardian topics that are specifically political/geopolitical
POLITICAL_GUARDIAN_TOPICS: tuple[str, ...] = (
    "geopolitics",
    "sanctions_trade",
    "russia_ukraine",
    "middle_east",
    "us_china",
    "us_politics",
    "elections",
    "global_politics",
    "eu_politics",
    "asia_politics",
    "latam_politics",
    "crypto_regulation",
)


class Command(BaseCommand):
    help = (
        "Fetch geopolitical and political news from RSS feeds and optionally backfill "
        "Guardian API history (~4 years). No API key needed for RSS-only mode."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--only",
            type=str,
            default="",
            help=(
                "Comma-separated topic slugs to collect (RSS and Guardian). "
                f"Empty = all. Available: {', '.join(TOPIC_SLUGS)}"
            ),
        )
        parser.add_argument(
            "--skip-rss",
            action="store_true",
            default=False,
            help="Skip the live RSS fetch step.",
        )
        parser.add_argument(
            "--max-per-feed",
            type=int,
            default=50,
            help="Max articles per RSS feed (default 50).",
        )
        parser.add_argument(
            "--sleep",
            type=float,
            default=0.3,
            help="Seconds between RSS HTTP calls (default 0.3).",
        )
        # ── Guardian history options ─────────────────────────────────────
        parser.add_argument(
            "--guardian-history",
            action="store_true",
            default=False,
            help=(
                "Also run a Guardian API historical backfill for political topics. "
                "Requires GUARDIAN_API_KEY env var."
            ),
        )
        parser.add_argument(
            "--days",
            type=int,
            default=365 * 4,
            help="Days back to fetch from Guardian (default ~4 years = 1461 days).",
        )
        parser.add_argument(
            "--to-date",
            type=str,
            default="",
            help="ISO end date for Guardian backfill (YYYY-MM-DD). Defaults to today.",
        )
        parser.add_argument(
            "--guardian-sleep",
            type=float,
            default=1.2,
            help="Seconds between Guardian API calls (default 1.2 — free tier is rate-limited).",
        )
        parser.add_argument(
            "--max-pages-per-month",
            type=int,
            default=40,
            help="Guardian pagination safety cap per topic per month (default 40).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=80,
            help="DB upsert batch size (default 80).",
        )

    def handle(self, *args, **options):
        only_raw = options["only"].strip()
        only_topics: set[str] | None = None
        if only_raw:
            only_topics = {s.strip() for s in only_raw.split(",") if s.strip()}
            self.stdout.write(f"Filtering to topics: {', '.join(sorted(only_topics))}")

        total_created = 0
        total_updated = 0

        # ── 1. Live RSS ──────────────────────────────────────────────────
        if not options["skip_rss"]:
            self.stdout.write(self.style.MIGRATE_HEADING("Step 1: RSS feeds"))
            items, statuses = fetch_geopolitical_news(
                max_per_feed=options["max_per_feed"],
                only_topics=only_topics,
                sleep_between=options["sleep"],
            )
            ok_feeds = sum(1 for s in statuses if s.get("ok"))
            failed = [s for s in statuses if not s.get("ok")]

            self.stdout.write(
                f"  Feeds OK: {ok_feeds}/{len(statuses)} · "
                f"articles fetched: {len(items)}"
            )
            for st in failed:
                self.stdout.write(
                    self.style.WARNING(
                        f"    FAIL [{st.get('topic_slug')}] {st.get('label')}: {st.get('error')}"
                    )
                )

            if items:
                c, u = upsert_news_articles(items)
                total_created += c
                total_updated += u
                self.stdout.write(f"  DB: created={c} updated={u}")

            # Per-topic summary
            by_topic: dict[str, int] = {}
            for item in items:
                slug = item.get("topic_slug") or "unknown"
                by_topic[slug] = by_topic.get(slug, 0) + 1
            if by_topic:
                self.stdout.write("  Breakdown by topic:")
                for slug, count in sorted(by_topic.items(), key=lambda x: -x[1]):
                    self.stdout.write(f"    {slug}: {count}")
        else:
            self.stdout.write("Skipping RSS (--skip-rss).")

        # ── 2. Guardian history ──────────────────────────────────────────
        if options["guardian_history"]:
            self.stdout.write(self.style.MIGRATE_HEADING("Step 2: Guardian historical backfill"))
            key = getattr(settings, "GUARDIAN_API_KEY", "") or ""
            if not key:
                self.stderr.write(
                    self.style.ERROR(
                        "GUARDIAN_API_KEY not set. "
                        "Get a free key at https://open-platform.theguardian.com/access/."
                    )
                )
            else:
                to_s = options["to_date"].strip()
                date_to = date.fromisoformat(to_s) if to_s else date.today()
                days = max(1, int(options["days"]))
                date_from = date_to - timedelta(days=days)

                # Pick political topics (or the user's --only filter intersected with Guardian topics)
                guardian_topics = [(s, q) for s, q in DEFAULT_TOPICS if s in POLITICAL_GUARDIAN_TOPICS]
                if only_topics:
                    guardian_topics = [(s, q) for s, q in guardian_topics if s in only_topics]

                if not guardian_topics:
                    self.stderr.write(
                        self.style.WARNING("No matching Guardian political topics after --only filter.")
                    )
                else:
                    self.stdout.write(
                        f"  Date range: {date_from} → {date_to} ({days} days) · "
                        f"topics: {', '.join(s for s, _ in guardian_topics)}"
                    )

                    batch_size = max(10, min(int(options["batch_size"]), 200))
                    batch: list = []

                    def flush():
                        nonlocal batch, total_created, total_updated
                        if not batch:
                            return
                        c, u = upsert_news_articles(batch)
                        total_created += c
                        total_updated += u
                        batch = []

                    for topic_slug, query in guardian_topics:
                        self.stdout.write(f"  → {topic_slug} …")
                        try:
                            for item in iter_guardian_topic_months(
                                api_key=key,
                                topic_slug=topic_slug,
                                query=query,
                                date_from=date_from,
                                date_to=date_to,
                                max_pages_per_month=int(options["max_pages_per_month"]),
                                sleep_sec=float(options["guardian_sleep"]),
                            ):
                                batch.append(item)
                                if len(batch) >= batch_size:
                                    flush()
                            flush()
                            self.stdout.write(f"    done (running total: created={total_created} updated={total_updated})")
                        except Exception as exc:
                            self.stderr.write(self.style.ERROR(f"    ERROR for {topic_slug}: {exc}"))

        # ── Summary ──────────────────────────────────────────────────────
        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. Total DB: created={total_created} updated={total_updated}"
            )
        )
