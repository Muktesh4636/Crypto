from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand

from markets.ml.model import PWM_MODEL_FAMILY, PWM_MODEL_NAME
from markets.services.binance import all_futures_symbols_by_quote_volume


class Command(BaseCommand):
    help = (
        "Train the pwm model separately for every Binance USDT perpetual (~534 coins): "
        "seed from old OHLCV, simulate shorts on historical bars, learn from each coin's trades."
    )

    def add_arguments(self, parser):
        parser.add_argument("--from", dest="date_from", type=str, default="2023-01-01")
        parser.add_argument("--to", dest="date_to", type=str, default="2026-05-16")
        parser.add_argument("--interval", type=str, default="1h")
        parser.add_argument("--force", action="store_true", help="Rebuild pwm models and backtest journal.")
        parser.add_argument(
            "--resume",
            action="store_true",
            default=True,
            help="Skip coins that already have pwm model + backtest trades (default: on).",
        )
        parser.add_argument(
            "--no-resume",
            dest="resume",
            action="store_false",
            help="Re-run every symbol even if pwm model already exists.",
        )

    def handle(self, *args, **options):
        symbols = all_futures_symbols_by_quote_volume()
        self.stdout.write(
            self.style.SUCCESS(
                f"Starting {PWM_MODEL_NAME} ({PWM_MODEL_FAMILY}) for {len(symbols)} futures symbols."
            )
        )
        self.stdout.write(
            "Each coin gets its own pwm_model_SYMBOL.pkl, historical paper trades, and journal retrain."
        )
        call_command(
            "train_historical_shorts",
            all_futures=True,
            date_from=options["date_from"],
            date_to=options["date_to"],
            interval=options["interval"],
            force=bool(options["force"]),
            resume=bool(options["resume"]),
        )
