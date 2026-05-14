from __future__ import annotations

import threading
import time

import requests

# USDT is treated as ~1 USD for INR conversion (display only).
_OPEN_ER_API = "https://open.er-api.com/v6/latest/USD"

_CACHE_LOCK = threading.Lock()
_USD_INR_RATE: float | None = None
_USD_INR_EXPIRES_MONO: float = 0.0
_USD_INR_TTL_SEC = 60.0


def get_usd_inr_rate(timeout: float = 8.0) -> float:
    """
    Spot USD→INR (cached). Used to convert USDT-denominated prices to approximate INR.
    """
    global _USD_INR_RATE, _USD_INR_EXPIRES_MONO
    now = time.monotonic()
    with _CACHE_LOCK:
        if _USD_INR_RATE is not None and now < _USD_INR_EXPIRES_MONO:
            return _USD_INR_RATE

    r = requests.get(_OPEN_ER_API, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if data.get("result") != "success":
        raise ValueError("Unexpected FX provider response")
    inr = float(data["rates"]["INR"])

    with _CACHE_LOCK:
        _USD_INR_RATE = inr
        _USD_INR_EXPIRES_MONO = time.monotonic() + _USD_INR_TTL_SEC
    return inr


def enrich_rows_inr(rows: list[dict], timeout: float = 8.0) -> tuple[list[dict], dict | None]:
    try:
        rate = get_usd_inr_rate(timeout=timeout)
    except (requests.RequestException, KeyError, TypeError, ValueError):
        rate = None

    if rate is None:
        for row in rows:
            row["last_price_inr"] = None
        return rows, None

    for row in rows:
        try:
            row["last_price_inr"] = float(row["last_price"]) * rate
        except (TypeError, ValueError):
            row["last_price_inr"] = None

    return rows, {
        "usd_inr": rate,
        "basis": "USDT last × USD→INR (open.er-api). USDT assumed ≈ 1 USD.",
    }
