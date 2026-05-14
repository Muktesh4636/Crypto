from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from sklearn.metrics import accuracy_score, classification_report, f1_score

from markets.ml.model import SignalModel
from markets.services.binance import fetch_historical_klines, top_futures_symbols_by_quote_volume
from markets.services.features import (
    FEATURE_COLUMNS,
    NEWS_MEMORY_DAYS,
    attach_direction_target,
    build_feature_frame,
    prepare_model_frame,
)


class Command(BaseCommand):
    help = "Train the initial short-only futures paper-trading signal model from Binance OHLCV."

    def add_arguments(self, parser):
        parser.add_argument("--symbol", type=str, default="")
        parser.add_argument("--symbols", type=str, default="")
        parser.add_argument("--universe", type=int, default=15)
        parser.add_argument("--interval", type=str, default="1h")
        parser.add_argument("--days", type=int, default=365 * 3)
        parser.add_argument("--horizon-bars", type=int, default=6)
        parser.add_argument("--sell-threshold", type=float, default=-0.006)

    def handle(self, *args, **options):
        symbol = options["symbol"].strip().upper()
        raw_symbols = options["symbols"].strip()
        interval = options["interval"].strip()
        days = max(90, int(options["days"]))
        universe = max(1, min(int(options["universe"]), 100))
        horizon_bars = max(1, int(options["horizon_bars"]))
        sell_threshold = float(options["sell_threshold"])

        if raw_symbols:
            symbols = [item.strip().upper() for item in raw_symbols.split(",") if item.strip()]
        elif symbol:
            symbols = [symbol]
        else:
            symbols = []
        if not symbols:
            symbols = top_futures_symbols_by_quote_volume(limit=universe)

        date_end = timezone.now()
        date_start = date_end - timedelta(days=days)

        trained_symbols: list[str] = []
        summary_rows: list[str] = []
        for sym in symbols:
            self.stdout.write(
                f"Fetching {sym} {interval} futures klines from {date_start.isoformat()} to {date_end.isoformat()}..."
            )
            klines = fetch_historical_klines(
                symbol=sym,
                interval=interval,
                start_time_ms=int(date_start.timestamp() * 1000),
                market="futures",
                end_time_ms=int(date_end.timestamp() * 1000),
            )
            if len(klines) < 500:
                self.stdout.write(self.style.WARNING(f"Skipping {sym}: not enough kline history."))
                continue
            feature_frame = build_feature_frame(klines)
            labeled = attach_direction_target(
                feature_frame,
                horizon_bars=horizon_bars,
                buy_threshold=1.0,
                sell_threshold=sell_threshold,
            )
            labeled.loc[labeled["target_name"] == "BUY", "target_name"] = "HOLD"
            labeled.loc[labeled["target_name"] == "HOLD", "target_class"] = 1
            labeled.loc[labeled["target_name"] == "SELL", "target_class"] = 0
            dataset = prepare_model_frame(labeled)
            if len(dataset) < 500:
                self.stdout.write(self.style.WARNING(f"Skipping {sym}: not enough usable feature rows."))
                continue
            split_idx = max(int(len(dataset) * 0.8), 1)
            train = dataset.iloc[:split_idx].copy()
            test = dataset.iloc[split_idx:].copy()
            if test.empty:
                self.stdout.write(self.style.WARNING(f"Skipping {sym}: need enough data for holdout test split."))
                continue

            model = SignalModel()
            train_metrics = model.train(train.loc[:, FEATURE_COLUMNS], train["target_class"])
            test_pred = model.predict_classes(test.loc[:, FEATURE_COLUMNS].fillna(0.0))

            test_accuracy = float(accuracy_score(test["target_class"], test_pred))
            test_macro_f1 = float(f1_score(test["target_class"], test_pred, average="macro"))
            class_breakdown = classification_report(
                test["target_class"],
                test_pred,
                output_dict=False,
                zero_division=0,
            )

            metadata = {
                "symbol": sym,
                "interval": interval,
                "market": "futures",
                "strategy_mode": "short_only",
                "analysis_mode": "per_symbol",
                "days": days,
                "price_memory_days": days,
                "news_memory_days": NEWS_MEMORY_DAYS,
                "universe": universe,
                "horizon_bars": horizon_bars,
                "sell_threshold": sell_threshold,
                "train_rows": len(train),
                "test_rows": len(test),
                "train_accuracy": train_metrics["train_accuracy"],
                "train_macro_f1": train_metrics["train_macro_f1"],
                "test_accuracy": test_accuracy,
                "test_macro_f1": test_macro_f1,
            }
            model.save(metadata, symbol=sym)
            trained_symbols.append(sym)
            summary_rows.append(
                f"{sym}: train/test={len(train)}/{len(test)} acc={test_accuracy:.3f} f1={test_macro_f1:.3f}"
            )
            self.stdout.write(self.style.SUCCESS(f"Saved per-symbol model for {sym}."))
            self.stdout.write("Class report:")
            self.stdout.write(class_breakdown)

        if not trained_symbols:
            self.stderr.write(self.style.ERROR("No usable futures symbol datasets were available for per-symbol training."))
            return

        self.stdout.write(self.style.SUCCESS("Per-symbol futures models trained and saved."))
        self.stdout.write("Symbols trained: " + ", ".join(trained_symbols))
        self.stdout.write("Feature columns: " + ", ".join(FEATURE_COLUMNS))
        self.stdout.write("Summary:")
        for row in summary_rows:
            self.stdout.write("  " + row)
