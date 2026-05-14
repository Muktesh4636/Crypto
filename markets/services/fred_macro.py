"""
FRED (Federal Reserve Economic Data) — official time series, not news articles.

Use this for historical time series at any depth (command default ≈ **1 year**; pass `--days` for more).
Register a free key: https://fred.stlouisfed.org/docs/api/api_key.html
"""

from __future__ import annotations

from datetime import date
from typing import Any

import requests

FRED_OBS_URL = "https://api.stlouisfed.org/fred/series/observations"

# Well-known series aligned with user themes (extend as needed).
DEFAULT_FRED_SERIES: tuple[tuple[str, str], ...] = (
    ("DFF", "Effective Federal Funds Rate"),
    ("CPIAUCSL", "Consumer Price Index for All Urban Consumers: All Items"),
    ("DCOILWTICO", "Crude Oil Prices: West Texas Intermediate"),
    ("T10Y2Y", "10-Year Treasury Constant Maturity Minus 2-Year"),
    ("M2SL", "M2 Money Stock"),
)


def fetch_fred_observations(
    *,
    api_key: str,
    series_id: str,
    observation_start: date,
    observation_end: date,
    timeout: float = 45.0,
) -> list[dict[str, Any]]:
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": observation_start.isoformat(),
        "observation_end": observation_end.isoformat(),
    }
    r = requests.get(FRED_OBS_URL, params=params, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    out: list[dict[str, Any]] = []
    for row in data.get("observations") or []:
        dstr = row.get("date")
        vraw = row.get("value")
        if not dstr:
            continue
        try:
            d = date.fromisoformat(dstr)
        except ValueError:
            continue
        val: float | None
        if vraw in (".", "", None):
            val = None
        else:
            try:
                val = float(vraw)
            except (TypeError, ValueError):
                val = None
        out.append({"date": d, "value": val, "raw_value": str(vraw or "")[:32]})
    return out
