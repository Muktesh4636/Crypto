from __future__ import annotations

from django.core.management.base import BaseCommand

from markets.trading.historical_backtest import clear_backtest_trades


class Command(BaseCommand):
    help = "Delete all historical backtest paper trades (notes starting with backtest:)."

    def add_arguments(self, parser):
        parser.add_argument("--symbol", type=str, default="", help="Only clear one symbol.")
        parser.add_argument("--yes", action="store_true", help="Skip confirmation.")

    def handle(self, *args, **options):
        symbol = options["symbol"].strip().upper() or None
        if not options["yes"]:
            scope = symbol or "ALL symbols"
            confirm = input(f"Delete backtest trades for {scope}? Type yes: ")
            if confirm.strip().lower() != "yes":
                self.stdout.write("Cancelled.")
                return
        deleted = clear_backtest_trades(symbol=symbol)
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} backtest trade rows."))
