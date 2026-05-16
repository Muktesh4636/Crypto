from __future__ import annotations

from typing import Any

import pandas as pd
from sklearn.metrics import accuracy_score, f1_score

from markets.ml.model import SignalModel, available_model_symbols
from markets.models import PaperTrade
from markets.services.features import FEATURE_COLUMNS, TARGET_NAME_TO_CLASS
from markets.trading.historical_backtest import BACKTEST_NOTE_PREFIX

PUMP_SCORE_COLUMN = "pump_manipulation_score_24"
NEWS_HYPE_COLUMN = "news_hype_score_24"
PUMP_FOCUS_THRESHOLD = 0.55
LOSS_WEIGHT_BASE = 12.0


def _snapshot_float(snapshot: dict, key: str) -> float:
    try:
        return float(snapshot.get(key, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def trade_sample_weight(trade: PaperTrade, snapshot: dict[str, Any]) -> float:
    """Heavier weight on short losses, especially pumps and bullish-news fade mistakes."""
    pump = _snapshot_float(snapshot, PUMP_SCORE_COLUMN)
    news_hype = _snapshot_float(snapshot, NEWS_HYPE_COLUMN)
    news_sentiment_24 = _snapshot_float(snapshot, "news_sentiment_24h")

    if trade.outcome == PaperTrade.Outcome.WIN:
        weight = 1.0
        if trade.action == PaperTrade.Action.SELL and pump >= PUMP_FOCUS_THRESHOLD:
            weight = 1.5
        return weight

    if trade.outcome == PaperTrade.Outcome.LOSS:
        weight = LOSS_WEIGHT_BASE
        if trade.action == PaperTrade.Action.SELL:
            if pump >= PUMP_FOCUS_THRESHOLD:
                weight = LOSS_WEIGHT_BASE + 6.0
            if news_hype >= 0.45 or news_sentiment_24 > 0.2:
                weight = max(weight, LOSS_WEIGHT_BASE + 4.0)
        return weight

    return 0.75


def _loss_count(
    symbol: str,
    *,
    backtest_only: bool,
    shorts_only: bool,
) -> int:
    qs = PaperTrade.objects.filter(symbol=symbol, outcome=PaperTrade.Outcome.LOSS)
    if backtest_only:
        qs = qs.filter(notes__startswith=BACKTEST_NOTE_PREFIX)
    if shorts_only:
        qs = qs.filter(action=PaperTrade.Action.SELL)
    return qs.count()


def trade_journal_training_frame(
    symbol: str | None = None,
    *,
    backtest_only: bool = False,
    shorts_only: bool = True,
    duplicate_losses: int = 0,
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    rows: list[dict[str, Any]] = []
    targets: list[int] = []
    weights: list[float] = []

    qs = PaperTrade.objects.exclude(outcome=PaperTrade.Outcome.OPEN)
    if symbol:
        qs = qs.filter(symbol=symbol)
    if backtest_only:
        qs = qs.filter(notes__startswith=BACKTEST_NOTE_PREFIX)
    if shorts_only:
        qs = qs.filter(action=PaperTrade.Action.SELL)
    qs = qs.order_by("closed_at")
    for trade in qs:
        snapshot = trade.signal_snapshot or {}
        row = {column: _snapshot_float(snapshot, column) for column in FEATURE_COLUMNS}
        if trade.outcome == PaperTrade.Outcome.WIN:
            target_name = trade.action
        elif trade.outcome == PaperTrade.Outcome.LOSS:
            target_name = "HOLD"
        else:
            target_name = "HOLD"
        weight = trade_sample_weight(trade, snapshot)
        rows.append(row)
        targets.append(TARGET_NAME_TO_CLASS[target_name])
        weights.append(weight)
        if trade.outcome == PaperTrade.Outcome.LOSS and duplicate_losses > 0:
            for _ in range(duplicate_losses):
                rows.append(dict(row))
                targets.append(TARGET_NAME_TO_CLASS[target_name])
                weights.append(weight * 1.25)

    if not rows:
        return pd.DataFrame(columns=FEATURE_COLUMNS), pd.Series(dtype=int), pd.Series(dtype=float)
    return (
        pd.DataFrame(rows, columns=FEATURE_COLUMNS).fillna(0.0),
        pd.Series(targets, dtype=int),
        pd.Series(weights, dtype=float),
    )


def retrain_from_losses(
    symbol: str,
    *,
    backtest_only: bool = False,
    shorts_only: bool = True,
    min_losses: int = 3,
    loss_duplicates: int = 2,
    additional_rounds: int = 80,
) -> dict[str, float | int | str]:
    """
    Per-coin update focused on losing short trades: duplicate loss rows and warm-start
    the existing model so it learns not to repeat those setups.
    """
    sym = symbol.upper()
    losses = _loss_count(sym, backtest_only=backtest_only, shorts_only=shorts_only)
    if losses < min_losses:
        raise ValueError(
            f"Need at least {min_losses} closed loss trades for {sym} before loss-focused retrain "
            f"(have {losses})."
        )

    features, labels, weights = trade_journal_training_frame(
        sym,
        backtest_only=backtest_only,
        shorts_only=shorts_only,
        duplicate_losses=loss_duplicates,
    )
    if len(features) < max(min_losses, 3):
        raise ValueError(f"Not enough journal rows to retrain {sym} from losses.")

    split_idx = max(int(len(features) * 0.85), 1)
    train_x = features.iloc[:split_idx]
    train_y = labels.iloc[:split_idx]
    train_w = weights.iloc[:split_idx]
    test_x = features.iloc[split_idx:]
    test_y = labels.iloc[split_idx:]

    model = SignalModel.load_if_available(symbol=sym) or SignalModel()
    if model_paths_has_trained_booster(sym):
        train_metrics = model.continue_training(
            train_x,
            train_y,
            sample_weight=train_w,
            additional_rounds=additional_rounds,
        )
        training_mode = "loss_focused_continue"
    else:
        train_metrics = model.train(train_x, train_y, sample_weight=train_w)
        training_mode = "loss_focused_fresh"

    result: dict[str, float | int | str] = {
        "symbol": sym,
        "train_rows": int(len(train_x)),
        "test_rows": int(len(test_x)),
        "loss_trades": int(losses),
        "train_accuracy": float(train_metrics["train_accuracy"]),
        "train_macro_f1": float(train_metrics["train_macro_f1"]),
        "training_mode": training_mode,
    }
    if not test_x.empty:
        test_pred = model.predict_classes(test_x.fillna(0.0))
        result["test_accuracy"] = float(accuracy_score(test_y, test_pred))
        result["test_macro_f1"] = float(f1_score(test_y, test_pred, average="macro"))
    loss_filter: dict[str, Any] = {"symbol": sym, "outcome": PaperTrade.Outcome.LOSS}
    if backtest_only:
        loss_filter["notes__startswith"] = BACKTEST_NOTE_PREFIX
    model.save(
        {
            **result,
            "analysis_mode": "per_symbol",
            "strategy_mode": "short_only",
            "retrained_from_trade_rows": int(len(features)),
            "retrained_from_losses": int(PaperTrade.objects.filter(**loss_filter).count()),
            "retrained_from_backtest_only": backtest_only,
            "retrained_shorts_only": shorts_only,
            "retrained_loss_focused": True,
        },
        symbol=sym,
    )
    return result


def model_paths_has_trained_booster(symbol: str) -> bool:
    from markets.ml.model import model_paths

    path, _ = model_paths(symbol)
    return path.exists()


def _retrain_one_symbol(
    symbol: str,
    *,
    backtest_only: bool = False,
    shorts_only: bool = True,
) -> dict[str, float | int | str]:
    losses = _loss_count(symbol, backtest_only=backtest_only, shorts_only=shorts_only)
    if losses >= 3:
        return retrain_from_losses(
            symbol,
            backtest_only=backtest_only,
            shorts_only=shorts_only,
            min_losses=3,
            loss_duplicates=2,
        )

    features, labels, weights = trade_journal_training_frame(
        symbol,
        backtest_only=backtest_only,
        shorts_only=shorts_only,
    )
    if len(features) < 10:
        raise ValueError("Need at least 10 closed short paper trades before retraining.")

    split_idx = max(int(len(features) * 0.8), 1)
    train_x = features.iloc[:split_idx]
    train_y = labels.iloc[:split_idx]
    train_w = weights.iloc[:split_idx]
    test_x = features.iloc[split_idx:]
    test_y = labels.iloc[split_idx:]

    model = SignalModel.load_if_available(symbol=symbol) or SignalModel()
    train_metrics = model.train(train_x, train_y, sample_weight=train_w)
    result: dict[str, float | int | str] = {
        "symbol": symbol,
        "train_rows": int(len(train_x)),
        "test_rows": int(len(test_x)),
        "train_accuracy": float(train_metrics["train_accuracy"]),
        "train_macro_f1": float(train_metrics["train_macro_f1"]),
    }
    if not test_x.empty:
        test_pred = model.predict_classes(test_x.fillna(0.0))
        result["test_accuracy"] = float(accuracy_score(test_y, test_pred))
        result["test_macro_f1"] = float(f1_score(test_y, test_pred, average="macro"))
    loss_filter = {"symbol": symbol, "outcome": PaperTrade.Outcome.LOSS}
    if backtest_only:
        loss_filter["notes__startswith"] = BACKTEST_NOTE_PREFIX
    model.save(
        {
            **result,
            "analysis_mode": "per_symbol",
            "strategy_mode": "short_only",
            "retrained_from_trade_rows": int(len(features)),
            "retrained_from_losses": int(PaperTrade.objects.filter(**loss_filter).count()),
            "retrained_from_backtest_only": backtest_only,
            "retrained_shorts_only": shorts_only,
        },
        symbol=symbol,
    )
    return result


def retrain_signal_model(
    symbol: str | None = None,
    *,
    backtest_only: bool = False,
    shorts_only: bool = True,
) -> dict[str, Any]:
    if symbol:
        return _retrain_one_symbol(symbol, backtest_only=backtest_only, shorts_only=shorts_only)

    symbols = sorted(
        set(PaperTrade.objects.exclude(outcome=PaperTrade.Outcome.OPEN).values_list("symbol", flat=True))
        | set(available_model_symbols())
    )
    results: dict[str, dict[str, float | int | str]] = {}
    skipped: dict[str, str] = {}
    for sym in symbols:
        try:
            results[sym] = _retrain_one_symbol(
                sym,
                backtest_only=backtest_only,
                shorts_only=shorts_only,
            )
        except ValueError as exc:
            skipped[sym] = str(exc)
    if not results:
        raise ValueError("Need at least 10 closed short paper trades for at least one symbol before retraining.")
    return {
        "analysis_mode": "per_symbol",
        "strategy_mode": "short_only",
        "trained_symbols": sorted(results.keys()),
        "skipped_symbols": skipped,
        "results": results,
    }
