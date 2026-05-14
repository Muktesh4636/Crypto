from __future__ import annotations

from typing import Any

import pandas as pd
from sklearn.metrics import accuracy_score, f1_score

from markets.ml.model import SignalModel
from markets.models import PaperTrade
from markets.services.features import FEATURE_COLUMNS, TARGET_NAME_TO_CLASS


def trade_journal_training_frame(symbol: str = "BTCUSDT") -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    rows: list[dict[str, Any]] = []
    targets: list[int] = []
    weights: list[float] = []

    qs = PaperTrade.objects.filter(symbol=symbol).exclude(outcome=PaperTrade.Outcome.OPEN).order_by("closed_at")
    for trade in qs:
        snapshot = trade.signal_snapshot or {}
        row = {column: float(snapshot.get(column, 0.0) or 0.0) for column in FEATURE_COLUMNS}
        if trade.outcome == PaperTrade.Outcome.WIN:
            target_name = trade.action
            sample_weight = 1.5
        elif trade.outcome == PaperTrade.Outcome.LOSS:
            target_name = "HOLD"
            sample_weight = 3.0
        else:
            target_name = "HOLD"
            sample_weight = 1.0
        rows.append(row)
        targets.append(TARGET_NAME_TO_CLASS[target_name])
        weights.append(sample_weight)

    if not rows:
        return pd.DataFrame(columns=FEATURE_COLUMNS), pd.Series(dtype=int), pd.Series(dtype=float)
    return (
        pd.DataFrame(rows, columns=FEATURE_COLUMNS).fillna(0.0),
        pd.Series(targets, dtype=int),
        pd.Series(weights, dtype=float),
    )


def retrain_signal_model(symbol: str = "BTCUSDT") -> dict[str, float | int | str]:
    features, labels, weights = trade_journal_training_frame(symbol)
    if len(features) < 20:
        raise ValueError("Need at least 20 closed paper trades before retraining.")

    split_idx = max(int(len(features) * 0.8), 1)
    train_x = features.iloc[:split_idx]
    train_y = labels.iloc[:split_idx]
    train_w = weights.iloc[:split_idx]
    test_x = features.iloc[split_idx:]
    test_y = labels.iloc[split_idx:]

    model = SignalModel.load_if_available() or SignalModel()
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
    model.save(
        {
            **result,
            "retrained_from_trade_rows": int(len(features)),
            "retrained_from_losses": int(
                PaperTrade.objects.filter(symbol=symbol, outcome=PaperTrade.Outcome.LOSS).count()
            ),
        }
    )
    return result
