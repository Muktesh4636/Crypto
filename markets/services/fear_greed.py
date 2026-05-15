from __future__ import annotations

import threading
import time

import pandas as pd
import requests

FNG_API = "https://api.alternative.me/fng/"
_CACHE_LOCK = threading.Lock()
_CACHE_SERIES: pd.Series | None = None
_CACHE_EXPIRES_MONO: float = 0.0
_CACHE_TTL_SEC = 3600.0


def fetch_fear_greed_series(*, limit: int = 2000, timeout: float = 20.0) -> pd.Series:
    """
    Return daily Crypto Fear & Greed values indexed by UTC midnight timestamps.
    Uses alternative.me public API (free, no key).
    """
    global _CACHE_SERIES, _CACHE_EXPIRES_MONO
    now = time.monotonic()
    with _CACHE_LOCK:
        if _CACHE_SERIES is not None and now < _CACHE_EXPIRES_MONO:
            return _CACHE_SERIES.copy()

    res = requests.get(
        FNG_API,
        params={"limit": max(1, min(limit, 2000)), "format": "json"},
        timeout=timeout,
    )
    res.raise_for_status()
    rows = res.json().get("data") or []
    if not rows:
        series = pd.Series(dtype=float)
    else:
        parsed: list[tuple[pd.Timestamp, float]] = []
        for item in rows:
            try:
                ts = pd.to_datetime(int(item["timestamp"]), unit="s", utc=True).normalize()
                parsed.append((ts, float(item["value"])))
            except (KeyError, TypeError, ValueError):
                continue
        if not parsed:
            series = pd.Series(dtype=float)
        else:
            frame = pd.DataFrame(parsed, columns=["timestamp", "value"]).drop_duplicates("timestamp")
            series = frame.set_index("timestamp")["value"].astype(float).sort_index()

    with _CACHE_LOCK:
        _CACHE_SERIES = series
        _CACHE_EXPIRES_MONO = now + _CACHE_TTL_SEC
    return series.copy()


def latest_fear_greed_value(timeout: float = 20.0) -> float | None:
    series = fetch_fear_greed_series(limit=5, timeout=timeout)
    if series.empty:
        return None
    return float(series.iloc[-1])
