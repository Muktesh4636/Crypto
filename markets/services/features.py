from __future__ import annotations

import math
import re
from collections.abc import Sequence
import numpy as np
import pandas as pd
import pandas_ta as ta
from django.utils import timezone

from markets.models import MacroObservation, NewsArticle
from markets.services.market_context import FeatureContext

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
MACRO_SERIES_IDS = ("DFF", "CPIAUCSL", "DCOILWTICO", "DTWEXBGS", "SP500", "DGS10")
MACRO_FEATURE_MAP: dict[str, tuple[str, str | None, str | None]] = {
    "DFF": ("macro_dff", "macro_dff_change_30d", None),
    "CPIAUCSL": ("macro_cpi", "macro_cpi_change_30d", None),
    "DCOILWTICO": ("macro_oil", "macro_oil_change_30d", None),
    "DTWEXBGS": ("macro_usd", "macro_usd_change_1d", "macro_usd_change_30d"),
    "SP500": ("macro_sp500", "macro_sp500_change_1d", "macro_sp500_change_30d"),
    "DGS10": ("macro_treasury10y", "macro_treasury10y_change_1d", "macro_treasury10y_change_30d"),
}
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
    "quote_volume_ratio_24",
    "trade_count_ratio_24",
    "avg_trade_size_ratio_24",
    "taker_buy_ratio_24",
    "taker_buy_ratio_168",
    "taker_volume_imbalance_24",
    "taker_sell_ratio_24",
    "taker_sell_volume_ratio_24",
    "body_ratio_24",
    "upper_wick_ratio_24",
    "lower_wick_ratio_24",
    "vol_spike_ratio_24",
    "distance_from_vwap_24",
    "poc_distance_24",
    "volume_above_poc_ratio_24",
    "global_long_short_ratio",
    "global_long_short_change_24",
    "funding_rate_latest",
    "funding_rate_cumulative_24",
    "open_interest_change_24",
    "oi_volume_ratio_24",
    "oi_price_divergence_24",
    "btc_lag_score_24",
    "btc_return_spread_24",
    "fear_greed_index",
    "fear_greed_change_7d",
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "is_us_session",
    "is_asia_session",
    "is_europe_session",
    "is_weekend",
    "large_trade_zscore_24",
    "whale_volume_spike_24",
    "whale_sell_pressure_24",
    "max_trade_size_ratio_24",
    "pump_manipulation_score_24",
    "news_hype_score_24",
    "order_book_imbalance",
    "order_book_bid_share",
    "order_book_spread_pct",
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
    "macro_usd",
    "macro_usd_change_1d",
    "macro_usd_change_30d",
    "macro_sp500",
    "macro_sp500_change_1d",
    "macro_sp500_change_30d",
    "macro_treasury10y",
    "macro_treasury10y_change_1d",
    "macro_treasury10y_change_30d",
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


def _align_series_to_index(series: pd.Series | None, index: pd.DatetimeIndex) -> pd.Series:
    if series is None or series.empty:
        return pd.Series(0.0, index=index)
    aligned = series.sort_index().reindex(index, method="ffill").ffill()
    return aligned.fillna(0.0).astype(float)


def _candle_ratio_features(frame: pd.DataFrame) -> pd.DataFrame:
    open_px = frame["open"]
    high = frame["high"]
    low = frame["low"]
    close = frame["close"]
    span = (high - low).replace(0, np.nan)
    body = (close - open_px).abs()
    upper_wick = high - np.maximum(open_px, close)
    lower_wick = np.minimum(open_px, close) - low
    out = pd.DataFrame(index=frame.index)
    out["body_ratio"] = (body / span).clip(0.0, 1.0)
    out["upper_wick_ratio"] = (upper_wick / span).clip(0.0, 1.0)
    out["lower_wick_ratio"] = (lower_wick / span).clip(0.0, 1.0)
    out["body_ratio_24"] = out["body_ratio"].rolling(24, min_periods=3).mean()
    out["upper_wick_ratio_24"] = out["upper_wick_ratio"].rolling(24, min_periods=3).mean()
    out["lower_wick_ratio_24"] = out["lower_wick_ratio"].rolling(24, min_periods=3).mean()
    return out


def _volume_profile_features(frame: pd.DataFrame, *, bars_24: int = 24) -> pd.DataFrame:
    typical = (frame["high"] + frame["low"] + frame["close"]) / 3.0
    volume = frame["volume"].astype(float)
    close = frame["close"].astype(float)
    vol_sum = volume.rolling(bars_24, min_periods=3).sum()
    vwap = (typical * volume).rolling(bars_24, min_periods=3).sum() / vol_sum.replace(0, np.nan)
    vol_threshold = volume.rolling(bars_24, min_periods=3).quantile(0.8)
    heavy_typical = typical.where(volume >= vol_threshold)
    poc_proxy = (
        (heavy_typical * volume).rolling(bars_24, min_periods=3).sum()
        / volume.where(volume >= vol_threshold).rolling(bars_24, min_periods=3).sum().replace(0, np.nan)
    ).fillna(vwap)
    above_poc_volume = volume.where(typical > poc_proxy, 0.0)
    out = pd.DataFrame(index=frame.index)
    out["distance_from_vwap_24"] = close / vwap.replace(0, np.nan) - 1.0
    out["poc_distance_24"] = close / poc_proxy.replace(0, np.nan) - 1.0
    out["volume_above_poc_ratio_24"] = above_poc_volume.rolling(bars_24, min_periods=3).sum() / vol_sum.replace(
        0, np.nan
    )
    return out.fillna(0.0)


def _btc_lag_features(frame: pd.DataFrame, btc_klines: Sequence[dict] | None) -> pd.DataFrame:
    out = pd.DataFrame(index=frame.index)
    out["btc_lag_score_24"] = 0.0
    out["btc_return_spread_24"] = 0.0
    if not btc_klines:
        return out
    btc = klines_to_frame(btc_klines)["close"].astype(float)
    btc_returns = btc.pct_change().rename("btc_return")
    coin_returns = frame["close"].astype(float).pct_change().rename("coin_return")
    merged = pd.DataFrame({"coin_return": coin_returns})
    merged["btc_return"] = btc_returns.reindex(coin_returns.index, method="ffill").fillna(0.0)
    lag_scores = []
    for lag in range(0, 7):
        shifted = merged["btc_return"].shift(lag)
        lag_scores.append(shifted.rolling(24, min_periods=6).corr(merged["coin_return"]))
    if lag_scores:
        stacked = pd.concat(lag_scores, axis=1)
        out["btc_lag_score_24"] = stacked.max(axis=1).fillna(0.0)
    coin_ret_24 = frame["close"].astype(float).pct_change(24)
    btc_ret_24 = btc.pct_change(24).reindex(frame.index, method="ffill").fillna(0.0)
    out["btc_return_spread_24"] = coin_ret_24 - btc_ret_24
    return out


def _time_pattern_features(index: pd.DatetimeIndex) -> pd.DataFrame:
    out = pd.DataFrame(index=index)
    hours = index.hour.astype(float)
    dow = index.dayofweek.astype(float)
    out["hour_sin"] = np.sin(2 * np.pi * hours / 24.0)
    out["hour_cos"] = np.cos(2 * np.pi * hours / 24.0)
    out["dow_sin"] = np.sin(2 * np.pi * dow / 7.0)
    out["dow_cos"] = np.cos(2 * np.pi * dow / 7.0)
    out["is_us_session"] = ((hours >= 13) & (hours < 22)).astype(float)
    out["is_asia_session"] = ((hours >= 0) & (hours < 8)).astype(float)
    out["is_europe_session"] = ((hours >= 7) & (hours < 16)).astype(float)
    out["is_weekend"] = (dow >= 5).astype(float)
    return out


def _pump_manipulation_features(frame: pd.DataFrame, *, bars_24: int = 24) -> pd.DataFrame:
    """Score likely pump/manipulation: volume spike + sharp rally + trade near local highs."""
    close = frame["close"].astype(float)
    quote_volume = frame["quote_volume"].astype(float)
    out = pd.DataFrame(index=frame.index)
    vol_median = quote_volume.rolling(bars_24, min_periods=3).median().replace(0, np.nan)
    vol_spike = (quote_volume / vol_median).fillna(0.0)
    pump_return = close.pct_change(bars_24).clip(lower=0.0).fillna(0.0)
    bars_30d = _bars_for_days(frame.index, 30)
    local_high = close.rolling(bars_30d, min_periods=max(24, bars_30d // 3)).max().replace(0, np.nan)
    near_high = (close / local_high).fillna(0.0)
    trade_count = frame["trade_count"].astype(float).replace(0, np.nan)
    avg_size = quote_volume / trade_count
    size_spike = avg_size / avg_size.rolling(bars_24, min_periods=3).mean().replace(0, np.nan)
    vol_component = (vol_spike - 1.0).clip(lower=0.0) / 4.0
    return_component = (pump_return * 20.0).clip(0.0, 1.0)
    high_component = near_high.clip(0.0, 1.0)
    size_component = (size_spike - 1.0).clip(lower=0.0) / 3.0
    out["pump_manipulation_score_24"] = (
        0.35 * vol_component + 0.30 * return_component + 0.20 * high_component + 0.15 * size_component
    ).clip(0.0, 1.0)
    return out


def _news_hype_features(index: pd.DatetimeIndex, news_features: pd.DataFrame) -> pd.DataFrame:
    """News-driven hype: elevated article flow with bullish tone (often precedes pumps)."""
    out = pd.DataFrame(index=index)
    count_24 = news_features.get("news_count_24h", pd.Series(0.0, index=index)).astype(float)
    count_7d = news_features.get("news_count_7d", pd.Series(0.0, index=index)).astype(float)
    sentiment_24 = news_features.get("news_sentiment_24h", pd.Series(0.0, index=index)).astype(float)
    count_baseline = (count_7d / 7.0).replace(0, np.nan)
    count_spike = (count_24 / count_baseline).fillna(0.0).clip(0.0, 5.0) / 5.0
    bullish = sentiment_24.clip(lower=0.0)
    out["news_hype_score_24"] = (0.55 * count_spike + 0.45 * bullish).clip(0.0, 1.0)
    return out


def _whale_behavior_features(frame: pd.DataFrame, *, bars_24: int = 24) -> pd.DataFrame:
    quote_volume = frame["quote_volume"].astype(float)
    trade_count = frame["trade_count"].astype(float).replace(0, np.nan)
    taker_buy_quote = frame["taker_buy_quote_volume"].astype(float)
    taker_sell_quote = (quote_volume - taker_buy_quote).clip(lower=0.0)
    avg_trade_size = quote_volume / trade_count
    vol_mean = quote_volume.rolling(bars_24, min_periods=3).mean()
    vol_std = quote_volume.rolling(bars_24, min_periods=3).std().replace(0, np.nan)
    trade_mean = avg_trade_size.rolling(bars_24, min_periods=3).mean()
    out = pd.DataFrame(index=frame.index)
    out["large_trade_zscore_24"] = ((quote_volume - vol_mean) / vol_std).clip(-5.0, 5.0).fillna(0.0)
    out["whale_volume_spike_24"] = (quote_volume / vol_mean.replace(0, np.nan)).fillna(0.0)
    sell_pressure = taker_sell_quote / quote_volume.replace(0, np.nan)
    size_spike = avg_trade_size / trade_mean.replace(0, np.nan)
    out["whale_sell_pressure_24"] = (
        (size_spike * sell_pressure).rolling(bars_24, min_periods=3).mean()
    ).fillna(0.0)
    out["max_trade_size_ratio_24"] = (
        avg_trade_size.rolling(bars_24, min_periods=3).max() / trade_mean.replace(0, np.nan)
    ).fillna(0.0)
    return out


def _order_book_features(
    index: pd.DatetimeIndex,
    *,
    order_book_series: pd.Series | None,
    latest_flow: dict | None,
) -> pd.DataFrame:
    out = pd.DataFrame(index=index)
    imbalance = _align_series_to_index(order_book_series, index)
    bid_share = pd.Series(0.0, index=index)
    spread = pd.Series(0.0, index=index)
    if latest_flow:
        if imbalance.iloc[-1] == 0.0 and latest_flow.get("order_book_imbalance") is not None:
            imbalance.iloc[-1] = float(latest_flow["order_book_imbalance"])
        if latest_flow.get("order_book_bid_share") is not None:
            bid_share.iloc[-1] = float(latest_flow["order_book_bid_share"])
        if latest_flow.get("order_book_spread_pct") is not None:
            spread.iloc[-1] = float(latest_flow["order_book_spread_pct"])
    out["order_book_imbalance"] = imbalance
    out["order_book_bid_share"] = bid_share
    out["order_book_spread_pct"] = spread
    return out


def _fear_greed_features(index: pd.DatetimeIndex, fear_greed_series: pd.Series | None) -> pd.DataFrame:
    out = pd.DataFrame(index=index)
    aligned = _align_series_to_index(fear_greed_series, index)
    out["fear_greed_index"] = aligned / 100.0
    out["fear_greed_change_7d"] = aligned.pct_change(_bars_for_days(index, 7)).replace(
        [np.inf, -np.inf], np.nan
    ).fillna(0.0)
    return out


def _futures_flow_features(
    frame: pd.DataFrame,
    *,
    funding_series: pd.Series | None,
    open_interest_series: pd.Series | None,
    global_long_short_series: pd.Series | None,
    latest_flow: dict | None,
) -> pd.DataFrame:
    out = pd.DataFrame(index=frame.index)
    funding = _align_series_to_index(funding_series, frame.index)
    oi = _align_series_to_index(open_interest_series, frame.index)
    ls_ratio = _align_series_to_index(global_long_short_series, frame.index)

    if latest_flow:
        if funding.iloc[-1] == 0.0 and latest_flow.get("last_funding_rate") is not None:
            funding.iloc[-1] = float(latest_flow["last_funding_rate"])
        if oi.iloc[-1] == 0.0 and latest_flow.get("open_interest_value_usdt") is not None:
            oi.iloc[-1] = float(latest_flow["open_interest_value_usdt"])
        if ls_ratio.iloc[-1] == 0.0 and latest_flow.get("global_long_short_ratio") is not None:
            ls_ratio.iloc[-1] = float(latest_flow["global_long_short_ratio"])

    out["funding_rate_latest"] = funding
    out["funding_rate_cumulative_24"] = funding.rolling(24, min_periods=1).sum()
    out["global_long_short_ratio"] = ls_ratio
    out["global_long_short_change_24"] = ls_ratio.pct_change(24).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    out["open_interest_change_24"] = oi.pct_change(24).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    quote_vol = frame["quote_volume"].astype(float).rolling(24, min_periods=3).sum()
    out["oi_volume_ratio_24"] = (oi / quote_vol.replace(0, np.nan)).fillna(0.0)
    price_change_24 = frame["close"].astype(float).pct_change(24)
    oi_change_24 = out["open_interest_change_24"]
    out["oi_price_divergence_24"] = np.sign(price_change_24.fillna(0.0)) * np.sign(oi_change_24.fillna(0.0)) * -1.0
    return out


def _macro_series(index: pd.DatetimeIndex) -> pd.DataFrame:
    if len(index) == 0:
        return _empty_aligned_features(index)
    start_date = (index.min() - pd.Timedelta(days=120)).date()
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
    empty_columns: list[str] = []
    for level_name, change_1d, change_30d in MACRO_FEATURE_MAP.values():
        empty_columns.append(level_name)
        if change_1d:
            empty_columns.append(change_1d)
        if change_30d:
            empty_columns.append(change_30d)
    if not rows:
        for column in empty_columns:
            out[column] = 0.0
        return out

    source = pd.DataFrame(rows)
    source["observation_date"] = pd.to_datetime(source["observation_date"], utc=True)
    for series_id, (level_name, change_1d, change_30d) in MACRO_FEATURE_MAP.items():
        series = source[source["series_id"] == series_id][["observation_date", "value"]].dropna()
        if series.empty:
            out[level_name] = 0.0
            if change_1d:
                out[change_1d] = 0.0
            if change_30d:
                out[change_30d] = 0.0
            continue
        series = series.set_index("observation_date")["value"].astype(float).sort_index()
        level = series.reindex(index, method="ffill").ffill().fillna(0.0)
        out[level_name] = level
        if change_1d:
            daily_change = series.pct_change(1).replace([np.inf, -np.inf], np.nan)
            out[change_1d] = daily_change.reindex(index, method="ffill").ffill().fillna(0.0)
        if change_30d:
            monthly_change = series.pct_change(periods=30).replace([np.inf, -np.inf], np.nan)
            out[change_30d] = monthly_change.reindex(index, method="ffill").ffill().fillna(0.0)
    return out


def build_feature_frame(
    klines: Sequence[dict],
    *,
    context: FeatureContext | None = None,
) -> pd.DataFrame:
    """
    Combine technical, news, macro, volume-profile, BTC-lag, and futures-flow features.
    """
    frame = klines_to_frame(klines)
    close = frame["close"]
    volume = frame["volume"]
    quote_volume = frame["quote_volume"]
    trade_count = frame["trade_count"].replace(0, np.nan)
    taker_buy_quote = frame["taker_buy_quote_volume"]
    taker_sell_quote = (quote_volume - taker_buy_quote).clip(lower=0.0)

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
    frame["quote_volume_ratio_24"] = quote_volume / quote_volume.rolling(24, min_periods=3).mean()
    frame["trade_count_ratio_24"] = trade_count / trade_count.rolling(24, min_periods=3).mean()

    avg_trade_size = quote_volume / trade_count
    frame["avg_trade_size_ratio_24"] = avg_trade_size / avg_trade_size.rolling(24, min_periods=3).mean()

    taker_buy_ratio = taker_buy_quote / quote_volume.replace(0, np.nan)
    taker_volume_imbalance = (taker_buy_quote - taker_sell_quote) / quote_volume.replace(0, np.nan)
    frame["taker_buy_ratio_24"] = taker_buy_ratio.rolling(24, min_periods=3).mean()
    frame["taker_buy_ratio_168"] = taker_buy_ratio.rolling(
        bars_7d, min_periods=max(24, bars_7d // 3)
    ).mean()
    frame["taker_volume_imbalance_24"] = taker_volume_imbalance.rolling(24, min_periods=3).mean()
    taker_sell_ratio = taker_sell_quote / quote_volume.replace(0, np.nan)
    frame["taker_sell_ratio_24"] = taker_sell_ratio.rolling(24, min_periods=3).mean()
    frame["taker_sell_volume_ratio_24"] = (
        taker_sell_quote.rolling(24, min_periods=3).sum()
        / quote_volume.rolling(24, min_periods=3).sum().replace(0, np.nan)
    ).fillna(0.0)
    rolling_median_qv = quote_volume.rolling(24, min_periods=3).median()
    frame["vol_spike_ratio_24"] = quote_volume / rolling_median_qv.replace(0, np.nan)
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

    candle_features = _candle_ratio_features(frame)
    volume_profile_features = _volume_profile_features(frame)
    time_features = _time_pattern_features(frame.index)
    whale_features = _whale_behavior_features(frame)
    btc_features = _btc_lag_features(frame, context.btc_klines if context else None)
    fear_greed_features = _fear_greed_features(
        frame.index,
        context.fear_greed_series if context else None,
    )
    futures_features = _futures_flow_features(
        frame,
        funding_series=context.funding_series if context else None,
        open_interest_series=context.open_interest_series if context else None,
        global_long_short_series=context.global_long_short_series if context else None,
        latest_flow=context.latest_flow if context else None,
    )
    order_book_features = _order_book_features(
        frame.index,
        order_book_series=context.order_book_imbalance_series if context else None,
        latest_flow=context.latest_flow if context else None,
    )
    news_features = _news_features(frame.index)
    pump_features = _pump_manipulation_features(frame)
    news_hype_features = _news_hype_features(frame.index, news_features)
    macro_features = _macro_series(frame.index)
    frame = (
        frame.join(candle_features, how="left")
        .join(volume_profile_features, how="left")
        .join(time_features, how="left")
        .join(pump_features, how="left")
        .join(news_hype_features, how="left")
        .join(whale_features, how="left")
        .join(btc_features, how="left")
        .join(fear_greed_features, how="left")
        .join(futures_features, how="left")
        .join(order_book_features, how="left")
        .join(news_features, how="left")
        .join(macro_features, how="left")
    )
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


def latest_feature_snapshot(
    klines: Sequence[dict],
    *,
    context: FeatureContext | None = None,
) -> dict[str, float | str]:
    """Return the latest fully-built feature row as a serializable dict."""
    frame = build_feature_frame(klines, context=context)
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
