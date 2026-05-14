from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import joblib
import numpy as np
import pandas as pd
from django.utils import timezone
from sklearn.metrics import accuracy_score, f1_score
from xgboost import XGBClassifier

from markets.services.features import FEATURE_COLUMNS, TARGET_CLASS_TO_NAME

MODEL_DIR = Path(
    os.environ.get("SIGNAL_MODEL_DIR", str(Path(__file__).resolve().parent / "models"))
)
MODEL_PATH = MODEL_DIR / "signal_model.pkl"
METADATA_PATH = MODEL_DIR / "signal_model.meta.json"
MODEL_GLOB = "signal_model_*.meta.json"


@dataclass
class SignalPrediction:
    label: str
    confidence: float
    probabilities: dict[str, float]
    model_version: str


class SignalModel:
    """Thin wrapper around an XGBoost 3-class classifier."""

    def __init__(
        self,
        model: XGBClassifier | None = None,
        *,
        feature_columns: tuple[str, ...] = FEATURE_COLUMNS,
        model_version: str | None = None,
    ) -> None:
        self.feature_columns = tuple(feature_columns)
        self.model_version = model_version or timezone.now().strftime("%Y%m%d%H%M%S")
        self.model = model or XGBClassifier(
            objective="multi:softprob",
            num_class=3,
            eval_metric="mlogloss",
            n_estimators=240,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.9,
            colsample_bytree=0.9,
            min_child_weight=2,
            random_state=42,
        )

    def _coerce_frame(self, features: pd.DataFrame | Mapping[str, Any]) -> pd.DataFrame:
        if isinstance(features, Mapping):
            frame = pd.DataFrame([{column: features.get(column, 0.0) for column in self.feature_columns}])
        else:
            frame = features.copy()
            for column in self.feature_columns:
                if column not in frame.columns:
                    frame[column] = 0.0
            frame = frame.loc[:, list(self.feature_columns)]
        frame = frame.replace([np.inf, -np.inf], np.nan).fillna(0.0)
        return frame

    @staticmethod
    def _normalize_predicted_classes(raw_pred: Any) -> np.ndarray:
        arr = np.asarray(raw_pred)
        if arr.ndim == 1:
            return arr.astype(int)
        if arr.ndim == 2:
            return np.argmax(arr, axis=1).astype(int)
        raise ValueError("Unexpected prediction shape from model.")

    def train(
        self,
        features: pd.DataFrame,
        labels: pd.Series | np.ndarray,
        *,
        sample_weight: pd.Series | np.ndarray | None = None,
    ) -> dict[str, float]:
        x_train = self._coerce_frame(features)
        y_train = np.asarray(labels, dtype=int)
        weights = None if sample_weight is None else np.asarray(sample_weight, dtype=float)
        self.model.fit(x_train, y_train, sample_weight=weights)
        pred = self._normalize_predicted_classes(self.model.predict(x_train))
        return {
            "train_accuracy": float(accuracy_score(y_train, pred)),
            "train_macro_f1": float(f1_score(y_train, pred, average="macro")),
            "rows": float(len(x_train)),
        }

    def predict_classes(self, features: pd.DataFrame | Mapping[str, Any]) -> np.ndarray:
        frame = self._coerce_frame(features)
        return self._normalize_predicted_classes(self.model.predict(frame))

    def predict(self, features: pd.DataFrame | Mapping[str, Any]) -> SignalPrediction:
        frame = self._coerce_frame(features)
        probabilities = self.model.predict_proba(frame)[0]
        best_idx = int(np.argmax(probabilities))
        label = TARGET_CLASS_TO_NAME.get(best_idx, "HOLD")
        probs = {
            TARGET_CLASS_TO_NAME[index]: float(probabilities[index])
            for index in range(len(probabilities))
        }
        return SignalPrediction(
            label=label,
            confidence=float(probabilities[best_idx]),
            probabilities=probs,
            model_version=self.model_version,
        )

    def save(self, metadata: Mapping[str, Any] | None = None, *, symbol: str | None = None) -> None:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        model_path, meta_path = model_paths(symbol)
        payload = {
            "model": self.model,
            "feature_columns": self.feature_columns,
            "model_version": self.model_version,
        }
        joblib.dump(payload, model_path)
        meta = {
            "model_version": self.model_version,
            "feature_columns": list(self.feature_columns),
            "saved_at": timezone.now().isoformat(),
        }
        if metadata:
            meta.update(dict(metadata))
        if symbol:
            meta["symbol"] = normalize_model_symbol(symbol)
        meta_path.write_text(json.dumps(meta, indent=2))

    @classmethod
    def load(cls, symbol: str | None = None) -> "SignalModel":
        model_path, _ = model_paths(symbol)
        payload = joblib.load(model_path)
        return cls(
            payload["model"],
            feature_columns=tuple(payload.get("feature_columns", FEATURE_COLUMNS)),
            model_version=payload.get("model_version"),
        )

    @classmethod
    def load_if_available(cls, symbol: str | None = None) -> "SignalModel | None":
        model_path, _ = model_paths(symbol)
        if not model_path.exists():
            return None
        return cls.load(symbol=symbol)


def normalize_model_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper()


def model_paths(symbol: str | None = None) -> tuple[Path, Path]:
    normalized = normalize_model_symbol(symbol) if symbol else ""
    if not normalized:
        return MODEL_PATH, METADATA_PATH
    return (
        MODEL_DIR / f"signal_model_{normalized}.pkl",
        MODEL_DIR / f"signal_model_{normalized}.meta.json",
    )


def available_model_symbols() -> list[str]:
    if not MODEL_DIR.exists():
        return []
    out: list[str] = []
    for path in sorted(MODEL_DIR.glob(MODEL_GLOB)):
        suffix = path.stem.removeprefix("signal_model_").removesuffix(".meta")
        symbol = normalize_model_symbol(suffix)
        if symbol:
            out.append(symbol)
    return out


def load_model_metadata(symbol: str | None = None) -> dict[str, Any]:
    _, meta_path = model_paths(symbol)
    if not meta_path.exists():
        return {}
    return json.loads(meta_path.read_text())


def load_all_model_metadata() -> dict[str, dict[str, Any]]:
    return {
        symbol: load_model_metadata(symbol)
        for symbol in available_model_symbols()
    }
