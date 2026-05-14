from django.core.management.base import BaseCommand

from markets.services.news_rss import fetch_world_news_sample
from markets.services.news_store import upsert_news_articles


class Command(BaseCommand):
    help = "Fetch configured RSS feeds and store new/updated rows in NewsArticle."

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-total",
            type=int,
            default=400,
            help="Max items to collect across all feeds (default 400, cap 2000).",
        )
        parser.add_argument(
            "--per-feed",
            type=int,
            default=100,
            help="Max items per feed before global cap (default 100, cap 200).",
        )

    def handle(self, *args, **options):
        max_total = options["max_total"]
        per_feed = options["per_feed"]
        items, statuses = fetch_world_news_sample(max_total=max_total, max_per_feed=per_feed)
        created, updated = upsert_news_articles(items)
        ok_feeds = sum(1 for s in statuses if s.get("ok"))
        self.stdout.write(
            self.style.SUCCESS(
                f"RSS feeds OK: {ok_feeds}/{len(statuses)} · "
                f"items fetched: {len(items)} · created: {created} · updated: {updated}"
            )
        )
        for st in statuses:
            if not st.get("ok"):
                self.stdout.write(self.style.WARNING(f"  fail {st.get('url')}: {st.get('error')}"))
