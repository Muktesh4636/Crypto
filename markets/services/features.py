from __future__ import annotations

import math
import re
from collections.abc import Sequence

import numpy as np
import pandas as pd
import pandas_ta as ta
from django.utils import timezone

from markets.models import MacroObservation, NewsArticle

POSITIVE_WORDS = {
    "adopt",
    "approval",
    "approve",
    "beat",
    "breakout",
    "bull",
    "bullish",
    "buy",
    "gain",
    "growth",
    "high",
    "launch",
    "partnership",
    "positive",
    "profit",
    "pump",
    "rally",
    "record",
    "recover",
    "rise",
    "surge",
    "up",
    "upgrade",
}
NEGATIVE_WORDS = {
    "ban",
    "bear",
    "bearish",
    "crackdown",
    "crash",
    "cut",
    "decline",
    "down",
    "drop",
    "dump",
    "fear",
    "hack",
    "high-risk",
    "inflation",
    "lawsuit",
    "loss",
    "negative",
    "recession",
    "reject",
    "risk",
    "sell",
    "slump",
    "uncertainty",
    "volatility",
    "warning",
}
MACRO_SERIES_IDS = ("DFF", "CPIAUCSL", "DCOILWTICO")
NEWS_MEMORY_DAYS = 365 * 3
FEATURE_COLUMNS = (
    "return_1",
    "return_4",
    "return_24",
    "return_168",
    "return_720",
    "return_2160",
    "rsi_14",
    "macd",
    "macd_signal",
    "macd_hist",
    "bb_upper",
    "bb_middle",
    "bb_lower",
    "bb_width",
    "volume_ratio_24",
    "volume_ratio_168",
    "volume_ratio_720",
    "volatility_24",
    "volatility_168",
    "volatility_720",
    "price_vs_sma_168",
    "price_vs_sma_720",
    "price_vs_history_mean",
    "distance_to_history_high",
    "distance_to_history_low",
    "news_sentiment_6h",
    "news_sentiment_24h",
    "news_sentiment_7d",
    "news_sentiment_30d",
    "news_sentiment_90d",
    "news_sentiment_history",
    "news_count_24h",
    "news_count_7d",
    "news_count_30d",
    "news_count_90d",
    "news_volume_ratio_30d",
    "macro_dff",
    "macro_dff_change_30d",
    "macro_cpi",
    "macro_cpi_change_30d",
    "macro_oil",
    "macro_oil_change_30d",
)
TARGET_CLASS_TO_NAME = {0: "SELL", 1: "HOLD", 2: "BUY"}
TARGET_NAME_TO_CLASS = {v: k for k, v in TARGET_CLASS_TO_NAME.items()}


def score_headline_sentiment(text: str) -> float:
    """Tiny lexical sentiment score in [-1, 1] for news titles/summaries."""
    tokens = re.findall(r"[A-Za-z][A-Za-z-]+", text.lower())
    if not tokens:
        return 0.0
    pos = sum(1 for token in tokens if token in POSITIVE_WORDS)
    neg = sum(1 for token in tokens if token in NEGATIVE_WORDS)
    total = pos + neg
    if total == 0:
        return 0.0
    return max(-1.0, min(1.0, (pos - neg) / total))


def klines_to_frame(klines: Sequence[dict]) -> pd.DataFrame:
    """Convert Binance kline rows into a sorted UTC-indexed DataFrame."""
    if not klines:
        raise ValueError("Expected at least one kline row.")
    frame = pd.DataFrame(list(klines)).copy()
    numeric_cols = (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "taker_buy_base_volume",
        "taker_buy_quote_volume",
    )
    for column in numeric_cols:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    for column in ("open_time", "close_time", "trade_count"):
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["timestamp"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
    frame = frame.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).set_index("timestamp")
    return frame


def _seconds_per_bar(index: pd.DatetimeIndex) -> int:
    if len(index) < 2:
        return 3600
    delta = int((index[1] - index[0]).total_seconds())
    return max(delta, 60)


def _bars_for_hours(index: pd.DatetimeIndex, hours: int) -> int:
    return max(1, int(math.ceil((hours * 3600) / _seconds_per_bar(index))))


def _bars_for_days(index: pd.DatetimeIndex, days: int) -> int:
    return _bars_for_hours(index, days * 24)


def _resample_rule(index: pd.DatetimeIndex) -> str:
    seconds = _seconds_per_bar(index)
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}min"
    return f"{seconds}s"


def _empty_aligned_features(index: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(index=index)


def _news_features(index: pd.DatetimeIndex) -> pd.DataFrame:
    if len(index) == 0:
        return _empty_aligned_features(index)
    lookback_start = (index.min() - pd.Timedelta(days=NEWS_MEMORY_DAYS)).to_pydatetime()
    lookback_end = index.max().to_pydatetime()
    empty_columns = (
        "news_sentiment_6h",
        "news_sentiment_24h",
        "news_sentiment_7d",
        "news_sentiment_30d",
        "news_sentiment_90d",
        "news_sentiment_history",
        "news_count_24h",
        "news_count_7d",
        "news_count_30d",
        "news_count_90d",
        "news_volume_ratio_30d",
    )
    rows = list(
        NewsArticle.objects.filter(
            published_at__isnull=False,
            published_at__gte=lookback_start,
            published_at__lte=lookback_end,
        )
        .order_by("published_at")
        .values("published_at", "title", "summary")
    )
    if not rows:
        return pd.DataFrame({column: pd.Series(0.0, index=index) for column in empty_columns})

    news_frame = pd.DataFrame(rows)
    news_frame["published_at"] = pd.to_datetime(news_frame["published_at"], utc=True)
    news_frame["score"] = (
        news_frame["title"].fillna("") + " " + news_frame["summary"].fillna("")
    ).map(score_headline_sentiment)
    news_frame = news_frame.set_index("published_at").sort_index()

    rule = _resample_rule(index)
    scored = news_frame["score"].resample(rule).agg(["sum", "count"]).rename(
        columns={"sum": "score_sum", "count": "news_count"}
    )
    scored = scored.reindex(index, fill_value=0.0)

    bars_6h = _bars_for_hours(index, 6)
    bars_24h = _bars_for_hours(index, 24)
    bars_7d = _bars_for_days(index, 7)
    bars_30d = _bars_for_days(index, 30)
    bars_90d = _bars_for_days(index, 90)
    history_min_bars = bars_30d
    count_6h = scored["news_count"].rolling(bars_6h, min_periods=1).sum()
    count_24h = scored["news_count"].rolling(bars_24h, min_periods=1).sum()
    count_7d = scored["news_count"].rolling(bars_7d, min_periods=1).sum()
    count_30d = scored["news_count"].rolling(bars_30d, min_periods=1).sum()
    count_90d = scored["news_count"].rolling(bars_90d, min_periods=1).sum()
    sum_6h = scored["score_sum"].rolling(bars_6h, min_periods=1).sum()
    sum_24h = scored["score_sum"].rolling(bars_24h, min_periods=1).sum()
    sum_7d = scored["score_sum"].rolling(bars_7d, min_periods=1).sum()
    sum_30d = scored["score_sum"].rolling(bars_30d, min_periods=1).sum()
    sum_90d = scored["score_sum"].rolling(bars_90d, min_periods=1).sum()
    history_count = scored["news_count"].expanding(min_periods=history_min_bars).sum()
    history_sum = scored["score_sum"].expanding(min_periods=history_min_bars).sum()
    history_avg_count = scored["news_count"].expanding(min_periods=history_min_bars).mean()
    recent_avg_count = scored["news_count"].rolling(bars_30d, min_periods=1).mean()

    out = pd.DataFrame(index=index)
    out["news_sentiment_6h"] = np.where(count_6h > 0, sum_6h / count_6h, 0.0)
    out["news_sentiment_24h"] = np.where(count_24h > 0, sum_24h / count_24h, 0.0)
    out["news_sentiment_7d"] = np.where(count_7d > 0, sum_7d / count_7d, 0.0)
    out["news_sentiment_30d"] = np.where(count_30d > 0, sum_30d / count_30d, 0.0)
    out["news_sentiment_90d"] = np.where(count_90d > 0, sum_90d / count_90d, 0.0)
    out["news_sentiment_history"] = np.where(history_count > 0, history_sum / history_count, 0.0)
    out["news_count_24h"] = count_24h.astype(float)
    out["news_count_7d"] = count_7d.astype(float)
    out["news_count_30d"] = count_30d.astype(float)
    out["news_count_90d"] = count_90d.astype(float)
    out["news_volume_ratio_30d"] = (recent_avg_count / history_avg_count.replace(0, np.nan)).fillna(0.0)
    return out


def _macro_series(index: pd.DatetimeIndex) -> pd.DataFrame:
    if len(index) == 0:
        return _empty_aligned_features(index)
    start_date = (index.min() - pd.Timedelta(days=90)).date()
    end_date = index.max().date()
    rows = list(
        MacroObservation.objects.filter(
            provider="fred",
            series_id__in=MACRO_SERIES_IDS,
            observation_date__gte=start_date,
            observation_date__lte=end_date,
        )
        .order_by("series_id", "observation_date")
        .values("series_id", "observation_date", "value")
    )
    out = pd.DataFrame(index=index)
    if not rows:
        for column in (
            "macro_dff",
            "macro_dff_change_30d",
            "macro_cpi",
            "macro_cpi_change_30d",
            "macro_oil",
            "macro_oil_change_30d",
        ):
            out[column] = 0.0
        return out

    source = pd.DataFrame(rows)
    source["observation_date"] = pd.to_datetime(source["observation_date"], utc=True)

    mapping = {
        "DFF": ("macro_dff", "macro_dff_change_30d"),
        "CPIAUCSL": ("macro_cpi", "macro_cpi_change_30d"),
        "DCOILWTICO": ("macro_oil", "macro_oil_change_30d"),
    }
    for series_id, (level_name, change_name) in mapping.items():
        series = source[source["series_id"] == series_id][["observation_date", "value"]].dropna()
        if series.empty:
            out[level_name] = 0.0
            out[change_name] = 0.0
            continue
        series = series.set_index("observation_date")["value"].astype(float).sort_index()
        changes = series.pct_change(periods=30).replace([np.inf, -np.inf], np.nan)
        out[level_name] = series.reindex(index, method="ffill").fillna(method="ffill").fillna(0.0)
        out[change_name] = changes.reindex(index, method="ffill").fillna(0.0)
    return out


def build_feature_frame(klines: Sequence[dict]) -> pd.DataFrame:
    """
    Combine technical, news, and macro features over a kline sequence.
    """
    frame = klines_to_frame(klines)
    close = frame["close"]
    volume = frame["volume"]

    frame["return_1"] = close.pct_change(1)
    frame["return_4"] = close.pct_change(4)
    frame["return_24"] = close.pct_change(24)
    bars_7d = _bars_for_days(frame.index, 7)
    bars_30d = _bars_for_days(frame.index, 30)
    bars_90d = _bars_for_days(frame.index, 90)
    frame["return_168"] = close.pct_change(bars_7d)
    frame["return_720"] = close.pct_change(bars_30d)
    frame["return_2160"] = close.pct_change(bars_90d)
    frame["volatility_24"] = close.pct_change().rolling(24, min_periods=3).std()
    frame["volatility_168"] = close.pct_change().rolling(bars_7d, min_periods=max(24, bars_7d // 3)).std()
    frame["volatility_720"] = close.pct_change().rolling(bars_30d, min_periods=max(bars_7d, bars_30d // 3)).std()
    frame["volume_ratio_24"] = volume / volume.rolling(24, min_periods=3).mean()
    frame["volume_ratio_168"] = volume / volume.rolling(bars_7d, min_periods=max(24, bars_7d // 3)).mean()
    frame["volume_ratio_720"] = volume / volume.rolling(bars_30d, min_periods=max(bars_7d, bars_30d // 3)).mean()
    frame["rsi_14"] = ta.rsi(close, length=14)

    macd = ta.macd(close, fast=12, slow=26, signal=9)
    if macd is not None:
        frame["macd"] = macd.iloc[:, 0]
        frame["macd_hist"] = macd.iloc[:, 1]
        frame["macd_signal"] = macd.iloc[:, 2]
    else:
        frame["macd"] = np.nan
        frame["macd_hist"] = np.nan
        frame["macd_signal"] = np.nan

    bbands = ta.bbands(close, length=20, std=2)
    if bbands is not None:
        frame["bb_lower"] = bbands.iloc[:, 0]
        frame["bb_middle"] = bbands.iloc[:, 1]
        frame["bb_upper"] = bbands.iloc[:, 2]
    else:
        frame["bb_lower"] = np.nan
        frame["bb_middle"] = np.nan
        frame["bb_upper"] = np.nan
    frame["bb_width"] = (frame["bb_upper"] - frame["bb_lower"]) / frame["bb_middle"].replace(0, np.nan)
    sma_168 = close.rolling(bars_7d, min_periods=max(24, bars_7d // 3)).mean()
    sma_720 = close.rolling(bars_30d, min_periods=max(bars_7d, bars_30d // 3)).mean()
    history_min_bars = max(24, bars_30d // 3)
    history_mean = close.expanding(min_periods=history_min_bars).mean()
    history_high = close.expanding(min_periods=history_min_bars).max()
    history_low = close.expanding(min_periods=history_min_bars).min()
    frame["price_vs_sma_168"] = close / sma_168 - 1.0
    frame["price_vs_sma_720"] = close / sma_720 - 1.0
    frame["price_vs_history_mean"] = close / history_mean - 1.0
    frame["distance_to_history_high"] = close / history_high - 1.0
    frame["distance_to_history_low"] = close / history_low - 1.0

    news_features = _news_features(frame.index)
    macro_features = _macro_series(frame.index)
    frame = frame.join(news_features, how="left").join(macro_features, how="left")
    frame = frame.replace([np.inf, -np.inf], np.nan)
    return frame


def attach_direction_target(
    frame: pd.DataFrame,
    *,
    horizon_bars: int = 6,
    buy_threshold: float = 0.006,
    sell_threshold: float = -0.006,
) -> pd.DataFrame:
    """Attach future return and 3-class direction labels for training."""
    if horizon_bars < 1:
        raise ValueError("horizon_bars must be >= 1")
    labeled = frame.copy()
    labeled["future_return"] = labeled["close"].shift(-horizon_bars) / labeled["close"] - 1.0
    labeled["target_name"] = "HOLD"
    labeled.loc[labeled["future_return"] >= buy_threshold, "target_name"] = "BUY"
    labeled.loc[labeled["future_return"] <= sell_threshold, "target_name"] = "SELL"
    labeled["target_class"] = labeled["target_name"].map(TARGET_NAME_TO_CLASS)
    return labeled


def latest_feature_snapshot(klines: Sequence[dict]) -> dict[str, float | str]:
    """Return the latest fully-built feature row as a serializable dict."""
    frame = build_feature_frame(klines)
    latest = frame.iloc[-1]
    payload: dict[str, float | str] = {
        "as_of": frame.index[-1].isoformat(),
        "generated_at": timezone.now().isoformat(),
    }
    for column in FEATURE_COLUMNS:
        value = latest.get(column)
        payload[column] = float(value) if value is not None and pd.notna(value) else 0.0
    payload["close"] = float(latest["close"])
    return payload


def prepare_model_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize NaNs/infs and keep only rows suitable for model fitting."""
    ready = frame.replace([np.inf, -np.inf], np.nan).copy()
    required = list(FEATURE_COLUMNS)
    if "target_class" in ready.columns:
        required.append("target_class")
    return ready.dropna(subset=required)
