from __future__ import annotations

from django.core.management.base import BaseCommand

from markets.management.commands.seed_model import (
    MIN_DATASET_ROWS,
    _build_symbol_dataset,
    _parse_window_date,
    _to_ms,
)
from markets.ml.model import SignalModel, model_paths
from markets.ml.retrain import retrain_from_losses, retrain_signal_model
from markets.services.binance import (
    all_futures_symbols_by_quote_volume,
    fetch_historical_klines,
    top_futures_symbols_by_quote_volume,
)
from markets.services.features import FEATURE_COLUMNS
from markets.trading.historical_backtest import clear_backtest_trades, run_symbol_backtest


class Command(BaseCommand):
    help = (
        "Train all coins on historical SHORT-only paper trades (2023→today): "
        "simulate down trades on old bars, learn from losses (pumps/news), retrain."
    )

    def add_arguments(self, parser):
        parser.add_argument("--symbol", type=str, default="")
        parser.add_argument("--symbols", type=str, default="")
        parser.add_argument("--universe", type=int, default=0)
        parser.add_argument("--all-futures", action="store_true")
        parser.add_argument("--from", dest="date_from", type=str, default="2023-01-01")
        parser.add_argument("--to", dest="date_to", type=str, default="2026-05-15")
        parser.add_argument("--interval", type=str, default="1h")
        parser.add_argument("--min-confidence", type=float, default=0.55)
        parser.add_argument("--pump-min-confidence", type=float, default=0.48)
        parser.add_argument("--warmup-bars", type=int, default=2400)
        parser.add_argument("--min-trades-for-retrain", type=int, default=5)
        parser.add_argument(
            "--min-losses-for-retrain",
            type=int,
            default=3,
            help="Minimum losing shorts required to run loss-focused learning per coin.",
        )
        parser.add_argument("--force", action="store_true", help="Overwrite models and clear backtest journal.")
        parser.add_argument("--skip-seed", action="store_true", help="Skip initial ML seed when model missing.")
        parser.add_argument(
            "--no-focus-pumps",
            dest="focus_pumps",
            action="store_false",
            help="Do not prioritize pump/manipulation + news-hype setups.",
        )

    def handle(self, *args, **options):
        symbol = options["symbol"].strip().upper()
        raw_symbols = options["symbols"].strip()
        universe = int(options["universe"])
        all_futures = bool(options["all_futures"]) or universe <= 0
        interval = options["interval"].strip()
        min_confidence = float(options["min_confidence"])
        pump_min_confidence = float(options["pump_min_confidence"])
        warmup_bars = max(500, int(options["warmup_bars"]))
        min_trades = max(1, int(options["min_trades_for_retrain"]))
        min_losses = max(1, int(options["min_losses_for_retrain"]))
        force = bool(options["force"])
        skip_seed = bool(options["skip_seed"])
        focus_pumps = bool(options["focus_pumps"])

        start_dt = _parse_window_date(options["date_from"])
        end_dt = _parse_window_date(options["date_to"], end_of_day=True)
        start_ms = _to_ms(start_dt)
        end_ms = _to_ms(end_dt)

        if raw_symbols:
            symbols = [s.strip().upper() for s in raw_symbols.split(",") if s.strip()]
        elif symbol:
            symbols = [symbol]
        elif all_futures or universe <= 0:
            symbols = all_futures_symbols_by_quote_volume()
        else:
            symbols = top_futures_symbols_by_quote_volume(limit=max(1, min(universe, 500)))

        self.stdout.write(
            f"Historical SHORT training {start_dt.date()}..{end_dt.date()} on {len(symbols)} symbols."
        )
        if focus_pumps:
            self.stdout.write("Pump/manipulation + news-hype setups are prioritized for short entries.")

        if force:
            deleted = clear_backtest_trades()
            self.stdout.write(f"Cleared {deleted} prior backtest trades.")

        btc_klines = fetch_historical_klines(
            symbol="BTCUSDT",
            interval=interval,
            start_time_ms=start_ms,
            market="futures",
            end_time_ms=end_ms,
        )

        trained: list[str] = []
        skipped: list[str] = []
        total = len(symbols)

        for index, sym in enumerate(symbols, start=1):
            model_path, _ = model_paths(sym)
            if model_path.exists() and not force:
                model = SignalModel.load(symbol=sym)
            elif skip_seed and not model_path.exists():
                skipped.append(f"{sym}: no model")
                continue
            else:
                self.stdout.write(f"[{index}/{total}] Seeding base model for {sym}...")
                dataset = _build_symbol_dataset(
                    sym,
                    interval=interval,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    btc_klines=btc_klines,
                    horizon_bars=6,
                    sell_threshold=-0.006,
                )
                if dataset is None or len(dataset) < MIN_DATASET_ROWS:
                    skipped.append(f"{sym}: insufficient history for seed")
                    self.stdout.write(self.style.WARNING(f"  Skip {sym}: not enough history."))
                    continue
                split_idx = max(int(len(dataset) * 0.85), 1)
                train = dataset.iloc[:split_idx]
                model = SignalModel()
                model.train(train.loc[:, FEATURE_COLUMNS], train["target_class"])
                model.save(
                    {
                        "symbol": sym,
                        "strategy_mode": "short_only",
                        "training_mode": "historical_short_seed",
                    },
                    symbol=sym,
                )

            self.stdout.write(f"[{index}/{total}] Paper-trading {sym} on historical bars...")
            try:
                stats = run_symbol_backtest(
                    sym,
                    model,
                    interval=interval,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    min_confidence=min_confidence,
                    warmup_bars=warmup_bars,
                    clear_existing=True,
                    phase_label="historical_short",
                    focus_pumps=focus_pumps,
                    pump_min_confidence=pump_min_confidence,
                )
            except ValueError as exc:
                skipped.append(f"{sym}: {exc}")
                self.stdout.write(self.style.WARNING(f"  Skip {sym}: {exc}"))
                continue

            loss_count = int(stats["losses"])
            trade_count = int(stats["trades_closed"])
            if loss_count < min_losses and trade_count < min_trades:
                skipped.append(f"{sym}: losses={loss_count} trades={trade_count}")
                self.stdout.write(
                    self.style.WARNING(
                        f"  {sym}: need >={min_losses} losses or >={min_trades} trades "
                        f"(got {loss_count} losses / {trade_count} trades), skip."
                    )
                )
                continue

            try:
                if loss_count >= min_losses:
                    metrics = retrain_from_losses(
                        sym,
                        backtest_only=True,
                        shorts_only=True,
                        min_losses=min_losses,
                        loss_duplicates=2,
                    )
                    mode = "loss_focused"
                else:
                    metrics = retrain_signal_model(
                        symbol=sym,
                        backtest_only=True,
                        shorts_only=True,
                    )
                    mode = "journal"
                trained.append(sym)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  {sym}: shorts closed={trade_count} "
                        f"(pump_focus={stats.get('pump_focus_trades', 0)}) "
                        f"wins={stats['wins']} losses={loss_count} "
                        f"learn_mode={mode} rows={metrics.get('train_rows', 0)}"
                    )
                )
            except ValueError as exc:
                skipped.append(f"{sym}: retrain {exc}")
                self.stdout.write(self.style.WARNING(f"  Retrain failed {sym}: {exc}"))

        self.stdout.write(self.style.SUCCESS(f"Done. Trained from history: {len(trained)} symbols."))
        if trained:
            self.stdout.write("Sample: " + ", ".join(trained[:25]) + ("..." if len(trained) > 25 else ""))
        if skipped:
            self.stdout.write(f"Skipped {len(skipped)} symbols.")
