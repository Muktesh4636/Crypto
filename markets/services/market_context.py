from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import pandas as pd
from django.utils import timezone

from markets.models import FuturesFlowSnapshot
from markets.services.binance import (
    fetch_futures_funding_rate_history,
    fetch_futures_global_long_short_ratio_history,
    fetch_futures_open_interest_history,
    fetch_futures_order_book_depth,
    fetch_historical_klines,
)
from markets.services.fear_greed import fetch_fear_greed_series


@dataclass
class FeatureContext:
    symbol: str = ""
    btc_klines: Sequence[dict] | None = None
    fear_greed_series: pd.Series | None = None
    funding_series: pd.Series | None = None
    open_interest_series: pd.Series | None = None
    global_long_short_series: pd.Series | None = None
    order_book_imbalance_series: pd.Series | None = None
    latest_flow: Mapping[str, float] | None = field(default=None)


def _series_from_rows(
    rows: Sequence[dict],
    *,
    time_key: str,
    value_key: str,
    unit: str = "ms",
) -> pd.Series:
    if not rows:
        return pd.Series(dtype=float)
    parsed: list[tuple[pd.Timestamp, float]] = []
    for row in rows:
        try:
            ts = pd.to_datetime(int(row[time_key]), unit=unit, utc=True)
            val = row.get(value_key)
            if val is None:
                continue
            parsed.append((ts, float(val)))
        except (KeyError, TypeError, ValueError):
            continue
    if not parsed:
        return pd.Series(dtype=float)
    frame = pd.DataFrame(parsed, columns=["timestamp", "value"]).drop_duplicates("timestamp")
    return frame.set_index("timestamp")["value"].astype(float).sort_index()


def latest_flow_snapshot(symbol: str) -> dict[str, float] | None:
    row = (
        FuturesFlowSnapshot.objects.filter(symbol=symbol.upper())
        .order_by("-bucket_time")
        .first()
    )
    if row is None:
        return None
    return {
        "global_long_short_ratio": row.global_long_short_ratio or 0.0,
        "top_trader_long_short_account_ratio": row.top_trader_long_short_account_ratio or 0.0,
        "top_trader_long_short_position_ratio": row.top_trader_long_short_position_ratio or 0.0,
        "taker_buy_sell_ratio": row.taker_buy_sell_ratio or 0.0,
        "taker_sell_volume": row.taker_sell_volume or 0.0,
        "last_funding_rate": row.last_funding_rate or 0.0,
        "open_interest_value_usdt": row.open_interest_value_usdt or 0.0,
        "quote_volume_24h": row.quote_volume_24h or 0.0,
        "order_book_imbalance": row.order_book_imbalance or 0.0,
        "order_book_bid_share": row.order_book_bid_share or 0.0,
        "order_book_spread_pct": row.order_book_spread_pct or 0.0,
    }


def _order_book_history_series(
    symbol: str,
    *,
    start_time_ms: int,
    end_time_ms: int,
) -> pd.Series:
    start_dt = pd.to_datetime(start_time_ms, unit="ms", utc=True)
    end_dt = pd.to_datetime(end_time_ms, unit="ms", utc=True)
    rows = list(
        FuturesFlowSnapshot.objects.filter(
            symbol=symbol.upper(),
            bucket_time__gte=start_dt.to_pydatetime(),
            bucket_time__lte=end_dt.to_pydatetime(),
            order_book_imbalance__isnull=False,
        )
        .order_by("bucket_time")
        .values("bucket_time", "order_book_imbalance")
    )
    if not rows:
        return pd.Series(dtype=float)
    parsed = [
        (pd.to_datetime(row["bucket_time"], utc=True), float(row["order_book_imbalance"]))
        for row in rows
        if row.get("order_book_imbalance") is not None
    ]
    if not parsed:
        return pd.Series(dtype=float)
    frame = pd.DataFrame(parsed, columns=["timestamp", "value"]).drop_duplicates("timestamp")
    return frame.set_index("timestamp")["value"].astype(float).sort_index()


def build_training_context(
    *,
    symbol: str,
    interval: str,
    start_time_ms: int,
    end_time_ms: int,
    btc_klines: Sequence[dict] | None = None,
    include_futures_flow: bool = True,
) -> FeatureContext:
    sym = symbol.upper()
    fear_greed = fetch_fear_greed_series()
    funding_rows: list[dict] = []
    oi_rows: list[dict] = []
    ls_rows: list[dict] = []
    if include_futures_flow and sym != "BTCUSDT":
        try:
            funding_rows = fetch_futures_funding_rate_history(
                sym,
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            )
        except Exception:
            funding_rows = []
        try:
            oi_rows = fetch_futures_open_interest_history(
                sym,
                period=_oi_period_for_interval(interval),
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            )
        except Exception:
            oi_rows = []
        try:
            ls_rows = fetch_futures_global_long_short_ratio_history(
                sym,
                period=_oi_period_for_interval(interval),
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            )
        except Exception:
            ls_rows = []

    if btc_klines is None and sym != "BTCUSDT":
        try:
            btc_klines = fetch_historical_klines(
                symbol="BTCUSDT",
                interval=interval,
                market="futures",
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            )
        except Exception:
            btc_klines = None

    return FeatureContext(
        symbol=sym,
        btc_klines=btc_klines,
        fear_greed_series=fear_greed,
        funding_series=_series_from_rows(funding_rows, time_key="fundingTime", value_key="fundingRate"),
        open_interest_series=_series_from_rows(
            oi_rows,
            time_key="timestamp",
            value_key="sumOpenInterestValue",
        ),
        global_long_short_series=_series_from_rows(
            ls_rows,
            time_key="timestamp",
            value_key="longShortRatio",
        ),
        order_book_imbalance_series=_order_book_history_series(
            sym,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        ),
    )


def build_live_context(
    *,
    symbol: str,
    interval: str,
    btc_klines: Sequence[dict] | None = None,
    include_recent_flow_history: bool = True,
) -> FeatureContext:
    end_ms = int(timezone.now().timestamp() * 1000)
    start_ms = end_ms - (30 * 24 * 3600 * 1000)
    if include_recent_flow_history:
        ctx = build_training_context(
            symbol=symbol,
            interval=interval,
            start_time_ms=start_ms,
            end_time_ms=end_ms,
            btc_klines=btc_klines,
            include_futures_flow=True,
        )
    else:
        ctx = FeatureContext(
            symbol=symbol.upper(),
            btc_klines=btc_klines,
            fear_greed_series=fetch_fear_greed_series(),
        )
    ctx.latest_flow = latest_flow_snapshot(symbol)
    try:
        live_book = fetch_futures_order_book_depth(symbol)
        if ctx.latest_flow is None:
            ctx.latest_flow = {}
        ctx.latest_flow = {
            **dict(ctx.latest_flow),
            "order_book_imbalance": float(live_book.get("order_book_imbalance") or 0.0),
            "order_book_bid_share": float(live_book.get("order_book_bid_share") or 0.0),
            "order_book_spread_pct": float(live_book.get("order_book_spread_pct") or 0.0),
        }
    except Exception:
        pass
    return ctx


def _oi_period_for_interval(interval: str) -> str:
    normalized = interval.strip().lower()
    if normalized.endswith("m"):
        return "5m"
    if normalized.endswith("h"):
        return "1h"
    if normalized.endswith("d"):
        return "1d"
    return "1h"
