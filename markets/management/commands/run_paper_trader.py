from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from markets.services.fx import get_usd_inr_rate
from markets.trading.paper_engine import DEFAULT_INTERVAL, DEFAULT_MARKET, DEFAULT_SYMBOL, DEFAULT_UNIVERSE, run_once


class Command(BaseCommand):
    help = "Run the AI paper trader on a loop (multi-coin short-only Binance futures, or once with --once)."

    def add_arguments(self, parser):
        parser.add_argument("--symbol", type=str, default="")
        parser.add_argument("--symbols", type=str, default="")
        parser.add_argument("--universe", type=int, default=DEFAULT_UNIVERSE)
        parser.add_argument("--interval", type=str, default=DEFAULT_INTERVAL)
        parser.add_argument("--market", type=str, default=DEFAULT_MARKET)
        parser.add_argument("--sleep-seconds", type=int, default=60)
        parser.add_argument("--risk-fraction", type=float, default=0.05)
        parser.add_argument("--stop-loss-pct", type=float, default=0.15)
        parser.add_argument("--take-profit-pct", type=float, default=0.08)
        parser.add_argument("--min-confidence", type=float, default=0.55)
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        symbol = options["symbol"].strip().upper()
        raw_symbols = options["symbols"].strip()
        interval = options["interval"].strip()
        market = options["market"].strip().lower()
        sleep_seconds = max(5, int(options["sleep_seconds"]))
        symbols = [item.strip().upper() for item in raw_symbols.split(",") if item.strip()]
        kwargs = {
            "symbol": symbol,
            "symbols": symbols,
            "universe": max(1, min(int(options["universe"]), 100)),
            "interval": interval,
            "market": market,
            "risk_fraction": float(options["risk_fraction"]),
            "stop_loss_pct": float(options["stop_loss_pct"]),
            "take_profit_pct": float(options["take_profit_pct"]),
            "min_confidence": float(options["min_confidence"]),
        }

        while True:
            try:
                usd_inr = get_usd_inr_rate()
            except Exception:
                usd_inr = None
            try:
                result = run_once(usd_inr=usd_inr, **kwargs)
                self.stdout.write(self.style.SUCCESS(str(result)))
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"paper trader iteration failed: {exc}"))
                if options["once"]:
                    return
            if options["once"]:
                return
            time.sleep(sleep_seconds)
