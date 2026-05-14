from datetime import date, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand

from markets.models import MacroObservation
from markets.services.fred_macro import DEFAULT_FRED_SERIES, fetch_fred_observations


class Command(BaseCommand):
    help = (
        "Load official FRED time series (Fed funds, CPI, WTI oil, …) into MacroObservation. "
        "Requires FRED_API_KEY (free). This is indicator data, not news text."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=365,
            help="Days of history ending at --to-date (default 1 year). Use e.g. 730 or 1460 for longer pulls.",
        )
        parser.add_argument(
            "--to-date",
            type=str,
            default="",
            help="ISO end date (YYYY-MM-DD). Default: today.",
        )
        parser.add_argument(
            "--series",
            type=str,
            default="",
            help="Comma-separated FRED series IDs. Default: built-in macro set.",
        )

    def handle(self, *args, **options):
        key = settings.FRED_API_KEY
        if not key:
            self.stderr.write(
                self.style.ERROR(
                    "Set FRED_API_KEY in the environment (https://fred.stlouisfed.org/docs/api/api_key.html)."
                )
            )
            return

        to_s = options["to_date"].strip()
        date_to = date.fromisoformat(to_s) if to_s else date.today()
        days = max(1, int(options["days"]))
        date_from = date_to - timedelta(days=days)
        if date_from > date_to:
            self.stderr.write(self.style.ERROR("Computed date range invalid."))
            return

        if options["series"].strip():
            series_list = []
            for sid in options["series"].split(","):
                sid = sid.strip().upper()
                if sid:
                    series_list.append((sid, sid))
        else:
            series_list = list(DEFAULT_FRED_SERIES)

        total = 0
        for series_id, title in series_list:
            self.stdout.write(f"  FRED {series_id} …")
            obs = fetch_fred_observations(
                api_key=key,
                series_id=series_id,
                observation_start=date_from,
                observation_end=date_to,
            )
            for row in obs:
                MacroObservation.objects.update_or_create(
                    provider="fred",
                    series_id=series_id,
                    observation_date=row["date"],
                    defaults={
                        "series_title": title[:255],
                        "value": row["value"],
                        "raw_value": row["raw_value"],
                    },
                )
                total += 1
        self.stdout.write(self.style.SUCCESS(f"Upserted {total} observation rows across {len(series_list)} series."))
