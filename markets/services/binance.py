from __future__ import annotations

import threading
import time

import requests

BINANCE_API = "https://api.binance.com"
BINANCE_FUTURES_API = "https://fapi.binance.com"
_KLINES_PAGE_LIMIT = 1000

_EXCHANGE_CACHE_LOCK = threading.Lock()
_EXCHANGE_SYMBOLS: frozenset[str] | None = None
_EXCHANGE_EXPIRES_MONO: float = 0.0
_FUTURES_CACHE_LOCK = threading.Lock()
_FUTURES_SYMBOLS: frozenset[str] | None = None
_FUTURES_EXPIRES_MONO: float = 0.0
# Symbols rarely change; avoid hitting exchangeInfo on every poll.
_EXCHANGE_CACHE_TTL_SEC = 300.0


def _spot_usdt_trading_symbols(exchange_info: dict) -> set[str]:
    eligible: set[str] = set()
    for s in exchange_info.get("symbols", []):
        if s.get("status") != "TRADING":
            continue
        if s.get("quoteAsset") != "USDT":
            continue
        if not _has_spot_permission(s):
            continue
        eligible.add(s["symbol"])
    return eligible


def _futures_usdt_trading_symbols(exchange_info: dict) -> set[str]:
    eligible: set[str] = set()
    for s in exchange_info.get("symbols", []):
        if s.get("status") != "TRADING":
            continue
        if s.get("quoteAsset") != "USDT":
            continue
        if s.get("contractType") != "PERPETUAL":
            continue
        if not s.get("symbol", "").endswith("USDT"):
            continue
        eligible.add(s["symbol"])
    return eligible


def _has_spot_permission(symbol: dict) -> bool:
    perms = symbol.get("permissions") or []
    if "SPOT" in perms:
        return True
    for group in symbol.get("permissionSets") or []:
        if "SPOT" in group:
            return True
    return False


def _eligible_spot_usdt_symbols(timeout: float) -> frozenset[str]:
    global _EXCHANGE_SYMBOLS, _EXCHANGE_EXPIRES_MONO
    now = time.monotonic()
    with _EXCHANGE_CACHE_LOCK:
        if _EXCHANGE_SYMBOLS is not None and now < _EXCHANGE_EXPIRES_MONO:
            return _EXCHANGE_SYMBOLS

    r = requests.get(f"{BINANCE_API}/api/v3/exchangeInfo", timeout=timeout)
    r.raise_for_status()
    symbols = frozenset(_spot_usdt_trading_symbols(r.json()))
    expires = time.monotonic() + _EXCHANGE_CACHE_TTL_SEC
    with _EXCHANGE_CACHE_LOCK:
        _EXCHANGE_SYMBOLS = symbols
        _EXCHANGE_EXPIRES_MONO = expires
    return symbols


def eligible_usdt_spot_symbols(timeout: float = 20.0) -> frozenset[str]:
    """Cached USDT spot symbols that are trading (matches REST filter logic)."""
    return _eligible_spot_usdt_symbols(timeout)


def _eligible_futures_usdt_symbols(timeout: float) -> frozenset[str]:
    global _FUTURES_SYMBOLS, _FUTURES_EXPIRES_MONO
    now = time.monotonic()
    with _FUTURES_CACHE_LOCK:
        if _FUTURES_SYMBOLS is not None and now < _FUTURES_EXPIRES_MONO:
            return _FUTURES_SYMBOLS

    r = requests.get(f"{BINANCE_FUTURES_API}/fapi/v1/exchangeInfo", timeout=timeout)
    r.raise_for_status()
    symbols = frozenset(_futures_usdt_trading_symbols(r.json()))
    expires = time.monotonic() + _EXCHANGE_CACHE_TTL_SEC
    with _FUTURES_CACHE_LOCK:
        _FUTURES_SYMBOLS = symbols
        _FUTURES_EXPIRES_MONO = expires
    return symbols


def eligible_usdt_futures_symbols(timeout: float = 20.0) -> frozenset[str]:
    """Cached Binance perpetual USDT futures symbols that are trading."""
    return _eligible_futures_usdt_symbols(timeout)


def fetch_top_coins_by_quote_volume(limit: int = 200, timeout: float = 20.0) -> list[dict]:
    """
    Return the top `limit` Binance **spot** USDT pairs by 24h quote volume (USDT).
    """
    if limit < 1:
        raise ValueError("limit must be >= 1")

    symbols = _eligible_spot_usdt_symbols(timeout=timeout)

    r2 = requests.get(f"{BINANCE_API}/api/v3/ticker/24hr", timeout=timeout)
    r2.raise_for_status()
    tickers = r2.json()

    rows: list[dict] = []
    for t in tickers:
        sym = t.get("symbol")
        if sym not in symbols:
            continue
        try:
            qv = float(t.get("quoteVolume", 0) or 0)
        except (TypeError, ValueError):
            qv = 0.0
        rows.append(
            {
                "symbol": sym,
                "base_asset": sym.removesuffix("USDT") if sym.endswith("USDT") else sym,
                "quote_asset": "USDT",
                "last_price": t.get("lastPrice"),
                "price_change_percent": t.get("priceChangePercent"),
                "open_price": t.get("openPrice"),
                "high_price": t.get("highPrice"),
                "low_price": t.get("lowPrice"),
                "volume": t.get("volume"),
                "quote_volume": qv,
                "weighted_avg_price": t.get("weightedAvgPrice"),
                "open_time": t.get("openTime"),
                "close_time": t.get("closeTime"),
                "count": t.get("count"),
            }
        )

    rows.sort(key=lambda x: x["quote_volume"], reverse=True)
    return rows[:limit]


def fetch_top_futures_by_quote_volume(limit: int = 200, timeout: float = 20.0) -> list[dict]:
    """
    Return the top `limit` Binance perpetual USDT futures contracts by 24h quote volume.
    """
    if limit < 1:
        raise ValueError("limit must be >= 1")

    symbols = _eligible_futures_usdt_symbols(timeout=timeout)

    res = requests.get(f"{BINANCE_FUTURES_API}/fapi/v1/ticker/24hr", timeout=timeout)
    res.raise_for_status()
    tickers = res.json()

    rows: list[dict] = []
    for t in tickers:
        sym = t.get("symbol")
        if sym not in symbols:
            continue
        try:
            qv = float(t.get("quoteVolume", 0) or 0)
        except (TypeError, ValueError):
            qv = 0.0
        rows.append(
            {
                "symbol": sym,
                "base_asset": sym.removesuffix("USDT") if sym.endswith("USDT") else sym,
                "quote_asset": "USDT",
                "last_price": t.get("lastPrice"),
                "price_change_percent": t.get("priceChangePercent"),
                "open_price": t.get("openPrice"),
                "high_price": t.get("highPrice"),
                "low_price": t.get("lowPrice"),
                "volume": t.get("volume"),
                "quote_volume": qv,
                "weighted_avg_price": t.get("weightedAvgPrice"),
                "open_time": t.get("openTime"),
                "close_time": t.get("closeTime"),
                "count": t.get("count"),
            }
        )

    rows.sort(key=lambda x: x["quote_volume"], reverse=True)
    return rows[:limit]


def top_futures_symbols_by_quote_volume(limit: int = 50, timeout: float = 20.0) -> list[str]:
    return [row["symbol"] for row in fetch_top_futures_by_quote_volume(limit=limit, timeout=timeout)]


def fetch_klines(
    *,
    symbol: str,
    interval: str = "1h",
    limit: int = 500,
    market: str = "spot",
    start_time_ms: int | None = None,
    end_time_ms: int | None = None,
    timeout: float = 20.0,
) -> list[dict]:
    """Fetch raw Binance klines and return them as typed dict rows."""
    if limit < 1 or limit > _KLINES_PAGE_LIMIT:
        raise ValueError(f"limit must be between 1 and {_KLINES_PAGE_LIMIT}")
    market_normalized = market.strip().lower()
    if market_normalized not in {"spot", "futures"}:
        raise ValueError("market must be 'spot' or 'futures'")
    params: dict[str, object] = {
        "symbol": symbol.upper(),
        "interval": interval,
        "limit": limit,
    }
    if start_time_ms is not None:
        params["startTime"] = int(start_time_ms)
    if end_time_ms is not None:
        params["endTime"] = int(end_time_ms)
    base_url = BINANCE_API if market_normalized == "spot" else BINANCE_FUTURES_API
    endpoint = "/api/v3/klines" if market_normalized == "spot" else "/fapi/v1/klines"
    res = requests.get(f"{base_url}{endpoint}", params=params, timeout=timeout)
    res.raise_for_status()
    rows: list[dict] = []
    for item in res.json():
        rows.append(
            {
                "open_time": int(item[0]),
                "open": item[1],
                "high": item[2],
                "low": item[3],
                "close": item[4],
                "volume": item[5],
                "close_time": int(item[6]),
                "quote_volume": item[7],
                "trade_count": int(item[8]),
                "taker_buy_base_volume": item[9],
                "taker_buy_quote_volume": item[10],
            }
        )
    return rows


def fetch_historical_klines(
    *,
    symbol: str,
    interval: str = "1h",
    start_time_ms: int,
    market: str = "spot",
    end_time_ms: int | None = None,
    timeout: float = 20.0,
) -> list[dict]:
    """
    Page through Binance klines from `start_time_ms` to `end_time_ms` (inclusive).
    """
    out: list[dict] = []
    cursor = int(start_time_ms)
    seen_open_times: set[int] = set()
    while True:
        chunk = fetch_klines(
            symbol=symbol,
            interval=interval,
            limit=_KLINES_PAGE_LIMIT,
            market=market,
            start_time_ms=cursor,
            end_time_ms=end_time_ms,
            timeout=timeout,
        )
        if not chunk:
            break
        added = 0
        for row in chunk:
            open_time = int(row["open_time"])
            if open_time in seen_open_times:
                continue
            seen_open_times.add(open_time)
            out.append(row)
            added += 1
        if added == 0:
            break
        if len(chunk) < _KLINES_PAGE_LIMIT:
            break
        cursor = int(chunk[-1]["close_time"]) + 1
        if end_time_ms is not None and cursor > int(end_time_ms):
            break
    out.sort(key=lambda row: int(row["open_time"]))
    return out
