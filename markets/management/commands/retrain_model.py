from __future__ import annotations

from django.core.management.base import BaseCommand

from markets.ml.retrain import retrain_signal_model


class Command(BaseCommand):
    help = "Retrain the signal model using outcomes from the paper-trade journal."

    def add_arguments(self, parser):
        parser.add_argument("--symbol", type=str, default="")

    def handle(self, *args, **options):
        symbol_raw = options["symbol"].strip().upper()
        symbol = symbol_raw or None
        try:
            metrics = retrain_signal_model(symbol=symbol)
        except ValueError as exc:
            self.stdout.write(self.style.WARNING(f"Skipped retraining: {exc}"))
            return
        self.stdout.write(self.style.SUCCESS("Signal model retrained from paper-trade history."))
        self.stdout.write(str(metrics))
