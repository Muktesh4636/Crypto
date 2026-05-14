from datetime import date, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand

from markets.services.guardian_news import DEFAULT_TOPICS, iter_guardian_topic_months
from markets.services.news_store import upsert_news_articles


class Command(BaseCommand):
    help = (
        "Backfill NewsArticle rows from Guardian Content API (~4 years by default). "
        "Requires GUARDIAN_API_KEY. Free tiers are rate limited — spread large jobs over days "
        "(use smaller --to-date / --month-chunk). "
        "Topic slugs (--only): geopolitics, fed_rates, etf_institutional_flows, inflation, oil_energy, macro."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=365 * 4,
            help="Calendar days back from --to-date (default ~4 years). Shorter: e.g. --days 365.",
        )
        parser.add_argument(
            "--to-date",
            type=str,
            default="",
            help="ISO end date inclusive (YYYY-MM-DD). Defaults to today UTC.",
        )
        parser.add_argument(
            "--only",
            type=str,
            default="",
            help="Comma-separated topic slugs from built-in catalog (subset of slugs below). Empty = all.",
        )
        parser.add_argument("--batch-size", type=int, default=80)
        parser.add_argument(
            "--max-pages-per-month",
            type=int,
            default=40,
            help="Safety cap on Guardian pagination per topic per month window.",
        )
        parser.add_argument(
            "--sleep",
            type=float,
            default=1.0,
            help="Seconds between Guardian HTTP calls (be polite to rate limits).",
        )

    def handle(self, *args, **options):
        key = settings.GUARDIAN_API_KEY
        if not key:
            self.stderr.write(
                self.style.ERROR(
                    "Set GUARDIAN_API_KEY in the environment (https://open-platform.theguardian.com/)."
                )
            )
            return

        to_s = options["to_date"].strip()
        if to_s:
            date_to = date.fromisoformat(to_s)
        else:
            date_to = date.today()
        days = max(1, int(options["days"]))
        date_from = date_to - timedelta(days=days)
        if date_from > date_to:
            self.stderr.write(self.style.ERROR("Computed date range invalid."))
            return

        only_raw = options["only"].strip()
        if only_raw:
            wanted = {s.strip() for s in only_raw.split(",") if s.strip()}
            topics = [(s, q) for s, q in DEFAULT_TOPICS if s in wanted]
            if not topics:
                self.stderr.write(self.style.ERROR("No matching topic slugs in --only."))
                return
        else:
            topics = list(DEFAULT_TOPICS)

        self.stdout.write(
            f"Guardian history {date_from} → {date_to} · topics: {', '.join(s for s, _ in topics)}"
        )

        batch_size = max(10, min(int(options["batch_size"]), 200))
        created = 0
        updated = 0
        batch: list = []

        def flush():
            nonlocal batch, created, updated
            if not batch:
                return
            c, u = upsert_news_articles(batch)
            created += c
            updated += u
            batch = []

        for topic_slug, query in topics:
            self.stdout.write(f"  → topic {topic_slug} …")
            for item in iter_guardian_topic_months(
                api_key=key,
                topic_slug=topic_slug,
                query=query,
                date_from=date_from,
                date_to=date_to,
                max_pages_per_month=int(options["max_pages_per_month"]),
                sleep_sec=float(options["sleep"]),
            ):
                batch.append(item)
                if len(batch) >= batch_size:
                    flush()
            flush()

        self.stdout.write(self.style.SUCCESS(f"Done. created={created} updated={updated}"))
