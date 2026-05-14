from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from sklearn.metrics import accuracy_score, classification_report, f1_score

from markets.ml.model import SignalModel
from markets.services.binance import fetch_historical_klines
from markets.services.features import FEATURE_COLUMNS, attach_direction_target, build_feature_frame, prepare_model_frame


class Command(BaseCommand):
    help = "Train the initial short-only futures paper-trading signal model from Binance OHLCV."

    def add_arguments(self, parser):
        parser.add_argument("--symbol", type=str, default="BTCUSDT")
        parser.add_argument("--interval", type=str, default="1h")
        parser.add_argument("--days", type=int, default=365 * 2)
        parser.add_argument("--horizon-bars", type=int, default=6)
        parser.add_argument("--sell-threshold", type=float, default=-0.006)

    def handle(self, *args, **options):
        symbol = options["symbol"].strip().upper()
        interval = options["interval"].strip()
        days = max(90, int(options["days"]))
        horizon_bars = max(1, int(options["horizon_bars"]))
        sell_threshold = float(options["sell_threshold"])

        date_end = timezone.now()
        date_start = date_end - timedelta(days=days)

        self.stdout.write(
            f"Fetching {symbol} {interval} futures klines from {date_start.isoformat()} to {date_end.isoformat()}..."
        )
        klines = fetch_historical_klines(
            symbol=symbol,
            interval=interval,
            start_time_ms=int(date_start.timestamp() * 1000),
            market="futures",
            end_time_ms=int(date_end.timestamp() * 1000),
        )
        if len(klines) < 500:
            self.stderr.write(self.style.ERROR("Not enough kline history returned to train a model."))
            return

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
            self.stderr.write(self.style.ERROR("Not enough usable feature rows after preprocessing."))
            return

        split_idx = max(int(len(dataset) * 0.8), 1)
        train = dataset.iloc[:split_idx].copy()
        test = dataset.iloc[split_idx:].copy()
        if test.empty:
            self.stderr.write(self.style.ERROR("Need enough data to keep a holdout test split."))
            return

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
            "symbol": symbol,
            "interval": interval,
            "market": "futures",
            "strategy_mode": "short_only",
            "days": days,
            "horizon_bars": horizon_bars,
            "sell_threshold": sell_threshold,
            "train_rows": len(train),
            "test_rows": len(test),
            "train_accuracy": train_metrics["train_accuracy"],
            "train_macro_f1": train_metrics["train_macro_f1"],
            "test_accuracy": test_accuracy,
            "test_macro_f1": test_macro_f1,
        }
        model.save(metadata)

        self.stdout.write(self.style.SUCCESS("Initial signal model trained and saved."))
        self.stdout.write(
            f"Rows train/test: {len(train)}/{len(test)} · "
            f"train acc={train_metrics['train_accuracy']:.3f} · "
            f"test acc={test_accuracy:.3f} · test macro-F1={test_macro_f1:.3f}"
        )
        self.stdout.write("Feature columns: " + ", ".join(FEATURE_COLUMNS))
        self.stdout.write("Class report:")
        self.stdout.write(class_breakdown)
