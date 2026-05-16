from __future__ import annotations

from django.core.management.base import BaseCommand
from markets.ml.model import SignalModel, available_model_symbols
from markets.ml.retrain import retrain_signal_model
from markets.services.binance import all_futures_symbols_by_quote_volume, top_futures_symbols_by_quote_volume
from markets.trading.historical_backtest import (
    _parse_window_date,
    clear_backtest_trades,
    run_symbol_backtest,
)


class Command(BaseCommand):
    help = (
        "Simulate short-only paper trades on historical bars (learn from wins/losses), "
        "optionally retrain models from the backtest journal."
    )

    def add_arguments(self, parser):
        parser.add_argument("--symbol", type=str, default="")
        parser.add_argument("--symbols", type=str, default="")
        parser.add_argument("--universe", type=int, default=15)
        parser.add_argument("--all-futures", action="store_true")
        parser.add_argument("--from", dest="date_from", type=str, default="2023-01-01")
        parser.add_argument("--to", dest="date_to", type=str, default="2026-05-15")
        parser.add_argument("--interval", type=str, default="1h")
        parser.add_argument("--min-confidence", type=float, default=0.55)
        parser.add_argument("--risk-fraction", type=float, default=0.05)
        parser.add_argument("--stop-loss-pct", type=float, default=0.15)
        parser.add_argument("--take-profit-pct", type=float, default=0.08)
        parser.add_argument("--warmup-bars", type=int, default=2400)
        parser.add_argument("--phase-label", type=str, default="phase1")
        parser.add_argument("--clear", action="store_true", help="Delete prior backtest trades before run.")
        parser.add_argument("--retrain", action="store_true", help="Retrain each symbol after backtest if enough trades.")
        parser.add_argument("--min-trades-for-retrain", type=int, default=15)
        parser.add_argument("--pump-min-confidence", type=float, default=0.48)
        parser.add_argument("--no-focus-pumps", dest="focus_pumps", action="store_false")

    def handle(self, *args, **options):
        symbol = options["symbol"].strip().upper()
        raw_symbols = options["symbols"].strip()
        universe = int(options["universe"])
        all_futures = bool(options["all_futures"])
        interval = options["interval"].strip()
        min_confidence = float(options["min_confidence"])
        risk_fraction = float(options["risk_fraction"])
        stop_loss_pct = float(options["stop_loss_pct"])
        take_profit_pct = float(options["take_profit_pct"])
        warmup_bars = max(500, int(options["warmup_bars"]))
        phase_label = options["phase_label"].strip() or "phase1"
        clear_existing = bool(options["clear"])
        do_retrain = bool(options["retrain"])
        min_trades = max(1, int(options["min_trades_for_retrain"]))
        focus_pumps = bool(options.get("focus_pumps", True))
        pump_min_confidence = float(options["pump_min_confidence"])

        start_dt = _parse_window_date(options["date_from"])
        end_dt = _parse_window_date(options["date_to"], end_of_day=True)
        if end_dt <= start_dt:
            self.stderr.write(self.style.ERROR("--to must be after --from."))
            return
        start_ms = int(start_dt.timestamp() * 1000)
        end_ms = int(end_dt.timestamp() * 1000)

        if raw_symbols:
            symbols = [s.strip().upper() for s in raw_symbols.split(",") if s.strip()]
        elif symbol:
            symbols = [symbol]
        elif all_futures:
            symbols = [s for s in all_futures_symbols_by_quote_volume() if s in set(available_model_symbols())]
            self.stdout.write(f"Backtesting {len(symbols)} symbols with trained models.")
        else:
            size = max(1, min(universe, 500))
            trained = set(available_model_symbols())
            symbols = [s for s in top_futures_symbols_by_quote_volume(limit=size) if s in trained]

        if not symbols:
            self.stderr.write(self.style.ERROR("No symbols to backtest. Train models with seed_model first."))
            return

        if clear_existing and not symbol and not raw_symbols:
            deleted = clear_backtest_trades()
            self.stdout.write(f"Cleared {deleted} prior backtest trades.")

        self.stdout.write(
            f"Historical backtest {start_dt.date()}..{end_dt.date()} on {len(symbols)} symbol(s)."
        )

        results: list[dict] = []
        skipped: list[str] = []
        for index, sym in enumerate(symbols, start=1):
            model = SignalModel.load_if_available(symbol=sym)
            if model is None:
                skipped.append(f"{sym}: no model")
                continue
            self.stdout.write(f"[{index}/{len(symbols)}] Backtesting {sym}...")
            try:
                stats = run_symbol_backtest(
                    sym,
                    model,
                    interval=interval,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    min_confidence=min_confidence,
                    risk_fraction=risk_fraction,
                    stop_loss_pct=stop_loss_pct,
                    take_profit_pct=take_profit_pct,
                    warmup_bars=warmup_bars,
                    clear_existing=clear_existing or bool(symbol or raw_symbols),
                    phase_label=phase_label,
                    focus_pumps=focus_pumps,
                    pump_min_confidence=pump_min_confidence,
                )
                results.append(stats)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  {sym}: opened={stats['trades_opened']} closed={stats['trades_closed']} "
                        f"wins={stats['wins']} losses={stats['losses']}"
                    )
                )
                if do_retrain and stats["trades_closed"] >= min_trades:
                    try:
                        metrics = retrain_signal_model(
                            symbol=sym,
                            backtest_only=True,
                            shorts_only=True,
                        )
                        sym_row = metrics.get("results", {}).get(sym, metrics)
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"  Retrained {sym}: train_acc={sym_row.get('train_accuracy', 0):.3f} "
                                f"losses_weighted=yes"
                            )
                        )
                    except ValueError as exc:
                        self.stdout.write(self.style.WARNING(f"  Retrain skipped {sym}: {exc}"))
            except ValueError as exc:
                skipped.append(f"{sym}: {exc}")
                self.stdout.write(self.style.WARNING(f"  Skipped {sym}: {exc}"))

        if not results:
            self.stderr.write(self.style.ERROR("No symbols backtested successfully."))
            return

        total_opened = sum(r["trades_opened"] for r in results)
        total_closed = sum(r["trades_closed"] for r in results)
        self.stdout.write(self.style.SUCCESS("Backtest complete."))
        self.stdout.write(f"Symbols: {len(results)} ok, {len(skipped)} skipped")
        self.stdout.write(f"Trades opened={total_opened} closed={total_closed}")
        if skipped:
            self.stdout.write("Skipped:")
            for line in skipped[:20]:
                self.stdout.write(f"  {line}")
            if len(skipped) > 20:
                self.stdout.write(f"  ... and {len(skipped) - 20} more")
