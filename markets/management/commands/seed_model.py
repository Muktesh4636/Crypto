from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any

import pandas as pd
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.dateparse import parse_date
from sklearn.metrics import accuracy_score, classification_report, f1_score

from markets.ml.model import SignalModel, model_paths
from markets.services.binance import fetch_historical_klines, top_futures_symbols_by_quote_volume
from markets.services.features import (
    FEATURE_COLUMNS,
    NEWS_MEMORY_DAYS,
    attach_direction_target,
    build_feature_frame,
    prepare_model_frame,
)
from markets.services.market_context import build_training_context

MIN_DATASET_ROWS = 500


def _parse_window_date(value: str, *, end_of_day: bool = False) -> datetime:
    parsed = parse_date(value.strip())
    if parsed is None:
        raise ValueError(f"Invalid date: {value!r}")
    if end_of_day:
        return datetime(
            parsed.year,
            parsed.month,
            parsed.day,
            23,
            59,
            59,
            tzinfo=dt_timezone.utc,
        )
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=dt_timezone.utc)


def _to_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _apply_short_only_labels(frame: pd.DataFrame) -> pd.DataFrame:
    labeled = frame.copy()
    labeled.loc[labeled["target_name"] == "BUY", "target_name"] = "HOLD"
    labeled.loc[labeled["target_name"] == "HOLD", "target_class"] = 1
    labeled.loc[labeled["target_name"] == "SELL", "target_class"] = 0
    return labeled


def _build_symbol_dataset(
    symbol: str,
    *,
    interval: str,
    start_ms: int,
    end_ms: int,
    btc_klines: list[dict] | None,
    horizon_bars: int,
    sell_threshold: float,
) -> pd.DataFrame | None:
    klines = fetch_historical_klines(
        symbol=symbol,
        interval=interval,
        start_time_ms=start_ms,
        market="futures",
        end_time_ms=end_ms,
    )
    if len(klines) < MIN_DATASET_ROWS:
        return None
    context = build_training_context(
        symbol=symbol,
        interval=interval,
        start_time_ms=start_ms,
        end_time_ms=end_ms,
        btc_klines=btc_klines,
    )
    feature_frame = build_feature_frame(klines, context=context)
    labeled = attach_direction_target(
        feature_frame,
        horizon_bars=horizon_bars,
        buy_threshold=1.0,
        sell_threshold=sell_threshold,
    )
    labeled = _apply_short_only_labels(labeled)
    dataset = prepare_model_frame(labeled)
    if len(dataset) < MIN_DATASET_ROWS:
        return None
    return dataset


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
        parser.add_argument("--two-stage", action="store_true")
        parser.add_argument("--phase1-from", type=str, default="2022-01-01")
        parser.add_argument("--phase1-to", type=str, default="2023-12-31")
        parser.add_argument("--phase2-from", type=str, default="2023-01-01")
        parser.add_argument("--phase2-to", type=str, default="")
        parser.add_argument("--phase2-rounds", type=int, default=120)
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **options):
        symbol = options["symbol"].strip().upper()
        raw_symbols = options["symbols"].strip()
        interval = options["interval"].strip()
        days = max(90, int(options["days"]))
        universe = max(1, min(int(options["universe"]), 100))
        horizon_bars = max(1, int(options["horizon_bars"]))
        sell_threshold = float(options["sell_threshold"])
        two_stage = bool(options["two_stage"])
        phase2_rounds = max(1, int(options["phase2_rounds"]))
        force = bool(options["force"])

        if raw_symbols:
            symbols = [item.strip().upper() for item in raw_symbols.split(",") if item.strip()]
        elif symbol:
            symbols = [symbol]
        else:
            symbols = []
        if not symbols:
            symbols = top_futures_symbols_by_quote_volume(limit=universe)

        if two_stage:
            self._train_two_stage(
                symbols=symbols,
                interval=interval,
                universe=universe,
                horizon_bars=horizon_bars,
                sell_threshold=sell_threshold,
                phase2_rounds=phase2_rounds,
                force=force,
                phase1_from=options["phase1_from"],
                phase1_to=options["phase1_to"],
                phase2_from=options["phase2_from"],
                phase2_to=options["phase2_to"],
            )
        else:
            self._train_single_stage(
                symbols=symbols,
                interval=interval,
                days=days,
                universe=universe,
                horizon_bars=horizon_bars,
                sell_threshold=sell_threshold,
            )

    def _train_single_stage(
        self,
        *,
        symbols: list[str],
        interval: str,
        days: int,
        universe: int,
        horizon_bars: int,
        sell_threshold: float,
    ) -> None:
        date_end = timezone.now()
        date_start = date_end - timedelta(days=days)
        start_ms = _to_ms(date_start)
        end_ms = _to_ms(date_end)

        btc_klines = fetch_historical_klines(
            symbol="BTCUSDT",
            interval=interval,
            start_time_ms=start_ms,
            market="futures",
            end_time_ms=end_ms,
        )

        trained_symbols: list[str] = []
        summary_rows: list[str] = []
        for sym in symbols:
            self.stdout.write(
                f"Fetching {sym} {interval} futures klines from {date_start.isoformat()} to {date_end.isoformat()}..."
            )
            dataset = _build_symbol_dataset(
                sym,
                interval=interval,
                start_ms=start_ms,
                end_ms=end_ms,
                btc_klines=btc_klines,
                horizon_bars=horizon_bars,
                sell_threshold=sell_threshold,
            )
            if dataset is None:
                self.stdout.write(self.style.WARNING(f"Skipping {sym}: not enough kline or feature history."))
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

        self._print_summary(trained_symbols, summary_rows)

    def _train_two_stage(
        self,
        *,
        symbols: list[str],
        interval: str,
        universe: int,
        horizon_bars: int,
        sell_threshold: float,
        phase2_rounds: int,
        force: bool,
        phase1_from: str,
        phase1_to: str,
        phase2_from: str,
        phase2_to: str,
    ) -> None:
        phase1_start = _parse_window_date(phase1_from)
        phase1_end = _parse_window_date(phase1_to, end_of_day=True)
        phase2_start = _parse_window_date(phase2_from)
        if phase2_to.strip():
            phase2_end = _parse_window_date(phase2_to, end_of_day=True)
        else:
            phase2_end = timezone.now()

        if phase1_end <= phase1_start:
            self.stderr.write(self.style.ERROR("phase1-to must be after phase1-from."))
            return
        if phase2_end <= phase2_start:
            self.stderr.write(self.style.ERROR("phase2-to must be after phase2-from."))
            return

        phase1_start_ms = _to_ms(phase1_start)
        phase1_end_ms = _to_ms(phase1_end)
        phase2_start_ms = _to_ms(phase2_start)
        phase2_end_ms = _to_ms(phase2_end)

        self.stdout.write(
            f"Two-stage training: phase1 {phase1_start.date()}..{phase1_end.date()}, "
            f"phase2 {phase2_start.date()}..{phase2_end.date()}"
        )

        phase1_btc = fetch_historical_klines(
            symbol="BTCUSDT",
            interval=interval,
            start_time_ms=phase1_start_ms,
            market="futures",
            end_time_ms=phase1_end_ms,
        )
        phase2_btc = fetch_historical_klines(
            symbol="BTCUSDT",
            interval=interval,
            start_time_ms=phase2_start_ms,
            market="futures",
            end_time_ms=phase2_end_ms,
        )

        trained_symbols: list[str] = []
        summary_rows: list[str] = []
        for sym in symbols:
            model_path, _ = model_paths(sym)
            if model_path.exists() and not force:
                self.stdout.write(
                    self.style.WARNING(f"Skipping {sym}: model exists (use --force to overwrite).")
                )
                continue

            self.stdout.write(f"Phase 1 dataset for {sym}...")
            phase1_dataset = _build_symbol_dataset(
                sym,
                interval=interval,
                start_ms=phase1_start_ms,
                end_ms=phase1_end_ms,
                btc_klines=phase1_btc,
                horizon_bars=horizon_bars,
                sell_threshold=sell_threshold,
            )
            if phase1_dataset is None:
                self.stdout.write(self.style.WARNING(f"Skipping {sym}: insufficient phase1 history."))
                continue

            phase1_split = max(int(len(phase1_dataset) * 0.9), 1)
            phase1_train = phase1_dataset.iloc[:phase1_split].copy()
            phase1_holdout = phase1_dataset.iloc[phase1_split:].copy()

            model = SignalModel()
            phase1_metrics = model.train(phase1_train.loc[:, FEATURE_COLUMNS], phase1_train["target_class"])
            phase1_holdout_acc = None
            phase1_holdout_f1 = None
            if not phase1_holdout.empty:
                phase1_pred = model.predict_classes(phase1_holdout.loc[:, FEATURE_COLUMNS].fillna(0.0))
                phase1_holdout_acc = float(accuracy_score(phase1_holdout["target_class"], phase1_pred))
                phase1_holdout_f1 = float(f1_score(phase1_holdout["target_class"], phase1_pred, average="macro"))

            self.stdout.write(f"Phase 2 dataset for {sym}...")
            phase2_dataset = _build_symbol_dataset(
                sym,
                interval=interval,
                start_ms=phase2_start_ms,
                end_ms=phase2_end_ms,
                btc_klines=phase2_btc,
                horizon_bars=horizon_bars,
                sell_threshold=sell_threshold,
            )
            if phase2_dataset is None:
                self.stdout.write(self.style.WARNING(f"Skipping {sym}: insufficient phase2 history."))
                continue

            split_idx = max(int(len(phase2_dataset) * 0.8), 1)
            phase2_train = phase2_dataset.iloc[:split_idx].copy()
            phase2_test = phase2_dataset.iloc[split_idx:].copy()
            if phase2_test.empty:
                self.stdout.write(self.style.WARNING(f"Skipping {sym}: need enough phase2 data for holdout split."))
                continue

            phase2_metrics = model.continue_training(
                phase2_train.loc[:, FEATURE_COLUMNS],
                phase2_train["target_class"],
                additional_rounds=phase2_rounds,
            )
            test_pred = model.predict_classes(phase2_test.loc[:, FEATURE_COLUMNS].fillna(0.0))
            test_accuracy = float(accuracy_score(phase2_test["target_class"], test_pred))
            test_macro_f1 = float(f1_score(phase2_test["target_class"], test_pred, average="macro"))
            class_breakdown = classification_report(
                phase2_test["target_class"],
                test_pred,
                output_dict=False,
                zero_division=0,
            )

            training_phases: list[dict[str, Any]] = [
                {
                    "phase": 1,
                    "from": phase1_start.date().isoformat(),
                    "to": phase1_end.date().isoformat(),
                    "rows": len(phase1_dataset),
                    "train_rows": len(phase1_train),
                    "holdout_rows": len(phase1_holdout),
                    "train_accuracy": phase1_metrics["train_accuracy"],
                    "train_macro_f1": phase1_metrics["train_macro_f1"],
                },
                {
                    "phase": 2,
                    "from": phase2_start.date().isoformat(),
                    "to": phase2_end.date().isoformat(),
                    "rows": len(phase2_dataset),
                    "train_rows": len(phase2_train),
                    "test_rows": len(phase2_test),
                    "additional_rounds": phase2_rounds,
                    "train_accuracy": phase2_metrics["train_accuracy"],
                    "train_macro_f1": phase2_metrics["train_macro_f1"],
                    "total_estimators": phase2_metrics["total_estimators"],
                },
            ]
            if phase1_holdout_acc is not None:
                training_phases[0]["holdout_accuracy"] = phase1_holdout_acc
                training_phases[0]["holdout_macro_f1"] = phase1_holdout_f1

            metadata = {
                "symbol": sym,
                "interval": interval,
                "market": "futures",
                "strategy_mode": "short_only",
                "analysis_mode": "per_symbol",
                "training_mode": "two_stage",
                "training_phases": training_phases,
                "news_memory_days": NEWS_MEMORY_DAYS,
                "universe": universe,
                "horizon_bars": horizon_bars,
                "sell_threshold": sell_threshold,
                "phase1_rows": len(phase1_dataset),
                "phase2_rows": len(phase2_dataset),
                "train_rows": len(phase2_train),
                "test_rows": len(phase2_test),
                "phase1_train_accuracy": phase1_metrics["train_accuracy"],
                "phase1_train_macro_f1": phase1_metrics["train_macro_f1"],
                "phase2_train_accuracy": phase2_metrics["train_accuracy"],
                "phase2_train_macro_f1": phase2_metrics["train_macro_f1"],
                "phase2_total_estimators": phase2_metrics["total_estimators"],
                "test_accuracy": test_accuracy,
                "test_macro_f1": test_macro_f1,
            }
            model.save(metadata, symbol=sym)
            trained_symbols.append(sym)
            summary_rows.append(
                f"{sym}: p1={len(phase1_dataset)} p2={len(phase2_dataset)} "
                f"test={len(phase2_test)} acc={test_accuracy:.3f} f1={test_macro_f1:.3f}"
            )
            self.stdout.write(self.style.SUCCESS(f"Saved two-stage model for {sym}."))
            self.stdout.write("Phase 2 holdout class report:")
            self.stdout.write(class_breakdown)

        self._print_summary(trained_symbols, summary_rows)

    def _print_summary(self, trained_symbols: list[str], summary_rows: list[str]) -> None:
        if not trained_symbols:
            self.stderr.write(self.style.ERROR("No usable futures symbol datasets were available for per-symbol training."))
            return

        self.stdout.write(self.style.SUCCESS("Per-symbol futures models trained and saved."))
        self.stdout.write("Symbols trained: " + ", ".join(trained_symbols))
        self.stdout.write("Feature columns: " + ", ".join(FEATURE_COLUMNS))
        self.stdout.write("Summary:")
        for row in summary_rows:
            self.stdout.write("  " + row)
