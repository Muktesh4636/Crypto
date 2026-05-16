from __future__ import annotations

import math
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
_REQUEST_RETRY_STATUSES = {418, 429}


def _get_with_retry(
    url: str,
    *,
    params: dict[str, object] | None = None,
    timeout: float = 20.0,
    max_retries: int = 6,
) -> requests.Response:
    delay_sec = 1.0
    last_response: requests.Response | None = None
    for attempt in range(max_retries):
        res = requests.get(url, params=params, timeout=timeout)
        last_response = res
        if res.status_code not in _REQUEST_RETRY_STATUSES:
            return res
        if attempt >= max_retries - 1:
            break
        retry_after = res.headers.get("Retry-After")
        sleep_for = float(retry_after) if retry_after and retry_after.isdigit() else delay_sec
        time.sleep(sleep_for)
        delay_sec = min(delay_sec * 2.0, 30.0)
    assert last_response is not None
    return last_response


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


def fetch_top_coins_by_quote_volume(limit: int | None = None, timeout: float = 20.0) -> list[dict]:
    """
    Return Binance **spot** USDT pairs ranked by 24h quote volume (USDT).
    When `limit` is None, return the full eligible universe.
    """
    if limit is not None and limit < 1:
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
    return rows if limit is None else rows[:limit]


def top_futures_symbols_by_quote_volume(limit: int = 50, timeout: float = 20.0) -> list[str]:
    return [row["symbol"] for row in fetch_top_futures_by_quote_volume(limit=limit, timeout=timeout)]


def all_futures_symbols_by_quote_volume(timeout: float = 20.0) -> list[str]:
    """All trading Binance USDT perpetual futures, ranked by 24h quote volume."""
    return [row["symbol"] for row in fetch_futures_ticker_rows(limit=None, timeout=timeout)]


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
    res = _get_with_retry(f"{base_url}{endpoint}", params=params, timeout=timeout)
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
        time.sleep(0.12)
    out.sort(key=lambda row: int(row["open_time"]))
    return out


def _to_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: object) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def fetch_futures_ticker_rows(limit: int | None = None, timeout: float = 20.0) -> list[dict]:
    """Return Binance perpetual USDT futures rows ranked by 24h quote volume."""
    if limit is not None and limit < 1:
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
        qv = _to_float(t.get("quoteVolume")) or 0.0
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
    return rows if limit is None else rows[:limit]


def fetch_top_futures_by_quote_volume(limit: int = 200, timeout: float = 20.0) -> list[dict]:
    """
    Return the top `limit` Binance perpetual USDT futures contracts by 24h quote volume.
    """
    return fetch_futures_ticker_rows(limit=limit, timeout=timeout)


def fetch_all_futures_mark_prices(timeout: float = 20.0) -> dict[str, dict]:
    """Return mark-price / funding metadata for all Binance USDT perpetual futures."""
    res = requests.get(f"{BINANCE_FUTURES_API}/fapi/v1/premiumIndex", timeout=timeout)
    res.raise_for_status()
    payload = res.json()
    out: dict[str, dict] = {}
    symbols = _eligible_futures_usdt_symbols(timeout=timeout)
    for item in payload:
        sym = item.get("symbol")
        if sym not in symbols:
            continue
        out[sym] = {
            "symbol": sym,
            "mark_price": _to_float(item.get("markPrice")),
            "index_price": _to_float(item.get("indexPrice")),
            "last_funding_rate": _to_float(item.get("lastFundingRate")),
            "next_funding_time": _to_int(item.get("nextFundingTime")),
            "time": _to_int(item.get("time")),
        }
    return out


def fetch_futures_open_interest(symbol: str, timeout: float = 20.0) -> dict:
    res = requests.get(
        f"{BINANCE_FUTURES_API}/fapi/v1/openInterest",
        params={"symbol": symbol.upper()},
        timeout=timeout,
    )
    res.raise_for_status()
    item = res.json()
    return {
        "symbol": item.get("symbol") or symbol.upper(),
        "open_interest": _to_float(item.get("openInterest")),
        "timestamp": _to_int(item.get("time")),
    }


def _fetch_latest_futures_ratio_series(
    *,
    endpoint: str,
    symbol: str,
    period: str,
    timeout: float = 20.0,
) -> dict:
    res = requests.get(
        f"{BINANCE_FUTURES_API}{endpoint}",
        params={"symbol": symbol.upper(), "period": period, "limit": 1},
        timeout=timeout,
    )
    res.raise_for_status()
    rows = res.json()
    if not rows:
        return {}
    return dict(rows[-1])


def fetch_futures_global_long_short_ratio(symbol: str, period: str = "5m", timeout: float = 20.0) -> dict:
    item = _fetch_latest_futures_ratio_series(
        endpoint="/futures/data/globalLongShortAccountRatio",
        symbol=symbol,
        period=period,
        timeout=timeout,
    )
    return {
        "symbol": item.get("symbol") or symbol.upper(),
        "long_short_ratio": _to_float(item.get("longShortRatio")),
        "long_account_ratio": _to_float(item.get("longAccount")),
        "short_account_ratio": _to_float(item.get("shortAccount")),
        "timestamp": _to_int(item.get("timestamp")),
    }


def fetch_futures_top_long_short_account_ratio(symbol: str, period: str = "5m", timeout: float = 20.0) -> dict:
    item = _fetch_latest_futures_ratio_series(
        endpoint="/futures/data/topLongShortAccountRatio",
        symbol=symbol,
        period=period,
        timeout=timeout,
    )
    return {
        "symbol": item.get("symbol") or symbol.upper(),
        "long_short_ratio": _to_float(item.get("longShortRatio")),
        "long_account_ratio": _to_float(item.get("longAccount")),
        "short_account_ratio": _to_float(item.get("shortAccount")),
        "timestamp": _to_int(item.get("timestamp")),
    }


def fetch_futures_top_long_short_position_ratio(symbol: str, period: str = "5m", timeout: float = 20.0) -> dict:
    item = _fetch_latest_futures_ratio_series(
        endpoint="/futures/data/topLongShortPositionRatio",
        symbol=symbol,
        period=period,
        timeout=timeout,
    )
    return {
        "symbol": item.get("symbol") or symbol.upper(),
        "long_short_ratio": _to_float(item.get("longShortRatio")),
        "long_position_ratio": _to_float(item.get("longAccount")),
        "short_position_ratio": _to_float(item.get("shortAccount")),
        "timestamp": _to_int(item.get("timestamp")),
    }


def fetch_futures_taker_buy_sell_ratio(symbol: str, period: str = "5m", timeout: float = 20.0) -> dict:
    item = _fetch_latest_futures_ratio_series(
        endpoint="/futures/data/takerlongshortRatio",
        symbol=symbol,
        period=period,
        timeout=timeout,
    )
    return {
        "symbol": item.get("symbol") or symbol.upper(),
        "buy_sell_ratio": _to_float(item.get("buySellRatio")),
        "buy_volume": _to_float(item.get("buyVol")),
        "sell_volume": _to_float(item.get("sellVol")),
        "timestamp": _to_int(item.get("timestamp")),
    }


def fetch_latest_closed_kline(
    symbol: str,
    *,
    interval: str = "1h",
    market: str = "futures",
    timeout: float = 20.0,
) -> dict:
    rows = fetch_klines(symbol=symbol, interval=interval, market=market, limit=2, timeout=timeout)
    if not rows:
        return {}
    if len(rows) >= 2:
        return rows[-2]
    return rows[-1]


def _max_pages_for_window(
    start_time_ms: int,
    end_time_ms: int | None,
    *,
    page_limit: int,
    interval_ms: int,
    min_pages: int = 20,
    max_pages_cap: int = 200,
) -> int:
    end_ms = int(end_time_ms if end_time_ms is not None else time.time() * 1000)
    span_ms = max(end_ms - int(start_time_ms), 0)
    estimated_points = max(span_ms // max(interval_ms, 1), 1)
    pages = math.ceil(estimated_points / max(page_limit, 1)) + 2
    return max(min_pages, min(int(pages), max_pages_cap))


def _period_to_ms(period: str) -> int:
    normalized = period.strip().lower()
    if normalized.endswith("m"):
        return int(normalized[:-1]) * 60 * 1000
    if normalized.endswith("h"):
        return int(normalized[:-1]) * 3600 * 1000
    if normalized.endswith("d"):
        return int(normalized[:-1]) * 24 * 3600 * 1000
    return 3600 * 1000


def _page_futures_data_rows(
    *,
    endpoint: str,
    params: dict[str, object],
    time_key: str = "timestamp",
    timeout: float = 20.0,
    max_pages: int = 20,
) -> list[dict]:
    out: list[dict] = []
    seen: set[int] = set()
    cursor = params.get("startTime")
    for _ in range(max_pages):
        page_params = dict(params)
        if cursor is not None:
            page_params["startTime"] = int(cursor)
        res = _get_with_retry(f"{BINANCE_FUTURES_API}{endpoint}", params=page_params, timeout=timeout)
        res.raise_for_status()
        rows = res.json()
        if not rows:
            break
        added = 0
        for row in rows:
            ts = _to_int(row.get(time_key))
            if ts is None or ts in seen:
                continue
            seen.add(ts)
            out.append(dict(row))
            added += 1
        if added == 0 or len(rows) < int(page_params.get("limit", 500)):
            break
        cursor = int(rows[-1][time_key]) + 1
        end_time = params.get("endTime")
        if end_time is not None and cursor > int(end_time):
            break
    out.sort(key=lambda row: int(row[time_key]))
    return out


def fetch_futures_funding_rate_history(
    symbol: str,
    *,
    start_time_ms: int,
    end_time_ms: int | None = None,
    timeout: float = 20.0,
) -> list[dict]:
    params: dict[str, object] = {
        "symbol": symbol.upper(),
        "startTime": int(start_time_ms),
        "limit": 1000,
    }
    if end_time_ms is not None:
        params["endTime"] = int(end_time_ms)
    max_pages = _max_pages_for_window(
        start_time_ms,
        end_time_ms,
        page_limit=1000,
        interval_ms=8 * 3600 * 1000,
    )
    return _page_futures_data_rows(
        endpoint="/fapi/v1/fundingRate",
        params=params,
        time_key="fundingTime",
        timeout=timeout,
        max_pages=max_pages,
    )


def fetch_futures_open_interest_history(
    symbol: str,
    *,
    period: str = "1h",
    start_time_ms: int,
    end_time_ms: int | None = None,
    timeout: float = 20.0,
) -> list[dict]:
    params: dict[str, object] = {
        "symbol": symbol.upper(),
        "period": period,
        "startTime": int(start_time_ms),
        "limit": 500,
    }
    if end_time_ms is not None:
        params["endTime"] = int(end_time_ms)
    interval_ms = _period_to_ms(period)
    max_pages = _max_pages_for_window(
        start_time_ms,
        end_time_ms,
        page_limit=500,
        interval_ms=interval_ms,
    )
    return _page_futures_data_rows(
        endpoint="/futures/data/openInterestHist",
        params=params,
        timeout=timeout,
        max_pages=max_pages,
    )


def fetch_futures_order_book_depth(symbol: str, *, limit: int = 100, timeout: float = 20.0) -> dict:
    """Return top-of-book depth stats for a Binance USDT perpetual futures symbol."""
    if limit < 5 or limit > 1000:
        raise ValueError("limit must be between 5 and 1000")
    res = requests.get(
        f"{BINANCE_FUTURES_API}/fapi/v1/depth",
        params={"symbol": symbol.upper(), "limit": int(limit)},
        timeout=timeout,
    )
    res.raise_for_status()
    payload = res.json()
    bids = payload.get("bids") or []
    asks = payload.get("asks") or []
    bid_depth = 0.0
    ask_depth = 0.0
    for price, qty in bids:
        p = _to_float(price) or 0.0
        q = _to_float(qty) or 0.0
        bid_depth += p * q
    for price, qty in asks:
        p = _to_float(price) or 0.0
        q = _to_float(qty) or 0.0
        ask_depth += p * q
    total_depth = bid_depth + ask_depth
    best_bid = _to_float(bids[0][0]) if bids else None
    best_ask = _to_float(asks[0][0]) if asks else None
    spread_pct = None
    if best_bid and best_ask and best_bid > 0:
        spread_pct = (best_ask - best_bid) / best_bid
    imbalance = None
    bid_share = None
    if total_depth > 0:
        imbalance = (bid_depth - ask_depth) / total_depth
        bid_share = bid_depth / total_depth
    return {
        "symbol": symbol.upper(),
        "bid_depth_usdt": bid_depth,
        "ask_depth_usdt": ask_depth,
        "order_book_imbalance": imbalance,
        "order_book_bid_share": bid_share,
        "order_book_spread_pct": spread_pct,
    }


def fetch_futures_global_long_short_ratio_history(
    symbol: str,
    *,
    period: str = "1h",
    start_time_ms: int,
    end_time_ms: int | None = None,
    timeout: float = 20.0,
) -> list[dict]:
    params: dict[str, object] = {
        "symbol": symbol.upper(),
        "period": period,
        "startTime": int(start_time_ms),
        "limit": 500,
    }
    if end_time_ms is not None:
        params["endTime"] = int(end_time_ms)
    interval_ms = _period_to_ms(period)
    max_pages = _max_pages_for_window(
        start_time_ms,
        end_time_ms,
        page_limit=500,
        interval_ms=interval_ms,
    )
    return _page_futures_data_rows(
        endpoint="/futures/data/globalLongShortAccountRatio",
        params=params,
        timeout=timeout,
        max_pages=max_pages,
    )
