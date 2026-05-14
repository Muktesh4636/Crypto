from __future__ import annotations

import threading
import time
from datetime import timedelta
from typing import Any, Mapping

from django.db.models import Sum
from django.utils import timezone

from markets.ml.model import SignalModel, available_model_symbols
from markets.models import PaperTrade
from markets.services.binance import fetch_historical_klines, top_futures_symbols_by_quote_volume
from markets.services.features import latest_feature_snapshot

DEFAULT_SYMBOL = "BTCUSDT"
DEFAULT_INTERVAL = "1h"
DEFAULT_MARKET = "futures"
DEFAULT_UNIVERSE = 15
STARTING_CAPITAL_INR = 100_000.0
DEFAULT_USD_INR = 83.0
PRICE_MEMORY_DAYS = 365 * 3
SNAPSHOT_REFRESH_SEC = 300

_MARKET_HISTORY_LOCK = threading.Lock()
_MARKET_HISTORY_CACHE: dict[tuple[str, str, str], dict[str, Any]] = {}


def starting_capital_usdt(usd_inr: float | None = None) -> float:
    rate = usd_inr if isinstance(usd_inr, (int, float)) and usd_inr and usd_inr > 0 else DEFAULT_USD_INR
    return STARTING_CAPITAL_INR / float(rate)


def _market_cache_key(symbol: str, interval: str, market: str) -> tuple[str, str, str]:
    return (symbol.strip().upper(), interval.strip(), market.strip().lower())


def _price_memory_window_ms() -> tuple[int, int]:
    end_dt = timezone.now()
    start_dt = end_dt - timedelta(days=PRICE_MEMORY_DAYS)
    return int(start_dt.timestamp() * 1000), int(end_dt.timestamp() * 1000)


def _merge_klines(existing: list[dict[str, Any]], updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {
        int(row["open_time"]): row
        for row in existing
    }
    for row in updates:
        merged[int(row["open_time"])] = row
    return [merged[key] for key in sorted(merged)]


def _load_price_memory_klines(
    *,
    symbol: str,
    interval: str,
    market: str,
) -> list[dict[str, Any]]:
    start_ms, end_ms = _price_memory_window_ms()
    cache_key = _market_cache_key(symbol, interval, market)
    now_mono = time.monotonic()
    with _MARKET_HISTORY_LOCK:
        cached = _MARKET_HISTORY_CACHE.get(cache_key)
    if cached is None:
        klines = fetch_historical_klines(
            symbol=symbol,
            interval=interval,
            market=market,
            start_time_ms=start_ms,
            end_time_ms=end_ms,
        )
    else:
        cached_klines = list(cached.get("klines") or [])
        if now_mono - float(cached.get("updated_mono", 0.0)) < SNAPSHOT_REFRESH_SEC:
            return [row for row in cached_klines if int(row["open_time"]) >= start_ms]
        refresh_start_ms = start_ms
        if cached_klines:
            refresh_start_ms = max(start_ms, int(cached_klines[-1]["close_time"]) + 1)
        updates = fetch_historical_klines(
            symbol=symbol,
            interval=interval,
            market=market,
            start_time_ms=refresh_start_ms,
            end_time_ms=end_ms,
        )
        klines = _merge_klines(cached_klines, updates)
    klines = [row for row in klines if int(row["open_time"]) >= start_ms]
    with _MARKET_HISTORY_LOCK:
        _MARKET_HISTORY_CACHE[cache_key] = {
            "klines": klines,
            "updated_mono": now_mono,
        }
    return klines


def load_market_snapshot(
    symbol: str = DEFAULT_SYMBOL,
    interval: str = DEFAULT_INTERVAL,
    market: str = DEFAULT_MARKET,
) -> dict[str, Any]:
    klines = _load_price_memory_klines(symbol=symbol, interval=interval, market=market)
    if not klines:
        raise RuntimeError(f"No {market} kline history returned for {symbol}.")
    snapshot = latest_feature_snapshot(klines)
    snapshot["symbol"] = symbol
    snapshot["interval"] = interval
    snapshot["market"] = market
    return snapshot


def normalize_symbol_list(symbols: list[str] | tuple[str, ...] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in symbols or []:
        sym = str(item or "").strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out


def tracked_symbols(
    *,
    symbols: list[str] | tuple[str, ...] | None = None,
    universe: int = DEFAULT_UNIVERSE,
) -> list[str]:
    explicit = normalize_symbol_list(symbols)
    if explicit:
        return explicit
    size = max(1, min(int(universe), 100))
    trained = set(available_model_symbols())
    if trained:
        ranked = [sym for sym in top_futures_symbols_by_quote_volume(limit=100) if sym in trained]
        if ranked:
            return ranked[:size]
        return sorted(trained)[:size]
    return top_futures_symbols_by_quote_volume(limit=size)


def latest_open_trade(symbol: str | None = None) -> PaperTrade | None:
    qs = PaperTrade.objects.filter(outcome=PaperTrade.Outcome.OPEN)
    if symbol:
        qs = qs.filter(symbol=symbol)
    return qs.order_by("-opened_at").first()


def open_trades(symbol: str | None = None) -> list[PaperTrade]:
    qs = PaperTrade.objects.filter(outcome=PaperTrade.Outcome.OPEN)
    if symbol:
        qs = qs.filter(symbol=symbol)
    return list(qs.order_by("-opened_at"))


def trade_pnl_usdt(trade: PaperTrade, price_usdt: float) -> float:
    if trade.action == PaperTrade.Action.BUY:
        return (price_usdt - trade.entry_price_usdt) * trade.quantity
    return (trade.entry_price_usdt - price_usdt) * trade.quantity


def _trade_pnl_pct(trade: PaperTrade, price_usdt: float) -> float:
    if trade.entry_price_usdt == 0:
        return 0.0
    signed_move = trade_pnl_usdt(trade, price_usdt) / (trade.quantity * trade.entry_price_usdt)
    return signed_move * 100.0


def portfolio_snapshot(
    *,
    symbol: str | None = None,
    current_price: float | None = None,
    current_prices: Mapping[str, float] | None = None,
    usd_inr: float | None = None,
    market: str = DEFAULT_MARKET,
) -> dict[str, Any]:
    open_rows = open_trades(symbol)
    price_map = dict(current_prices or {})
    if symbol and current_price is not None:
        price_map[symbol] = float(current_price)
    for trade in open_rows:
        if trade.symbol not in price_map:
            price_map[trade.symbol] = float(load_market_snapshot(symbol=trade.symbol, market=market)["close"])
    starting_usdt = starting_capital_usdt(usd_inr)
    closed_qs = PaperTrade.objects.exclude(outcome=PaperTrade.Outcome.OPEN)
    if symbol:
        closed_qs = closed_qs.filter(symbol=symbol)
    realized_pnl_usdt = float(closed_qs.aggregate(total=Sum("pnl_usdt"))["total"] or 0.0)
    unrealized_pnl_usdt = 0.0
    for trade in open_rows:
        price = price_map.get(trade.symbol)
        if price is None:
            continue
        unrealized_pnl_usdt += trade_pnl_usdt(trade, price)
    equity_usdt = starting_usdt + realized_pnl_usdt + unrealized_pnl_usdt
    closed_count = closed_qs.count()
    wins = closed_qs.filter(outcome=PaperTrade.Outcome.WIN).count()
    losses = closed_qs.filter(outcome=PaperTrade.Outcome.LOSS).count()
    flat = closed_qs.filter(outcome=PaperTrade.Outcome.FLAT).count()
    win_rate = (wins / closed_count) if closed_count else 0.0
    rate = usd_inr if isinstance(usd_inr, (int, float)) and usd_inr and usd_inr > 0 else DEFAULT_USD_INR
    current_price_value = None
    if symbol:
        current_price_value = price_map.get(symbol)
    elif len(open_rows) == 1:
        current_price_value = price_map.get(open_rows[0].symbol)
    return {
        "symbol": symbol or ("ALL" if len(open_rows) != 1 else open_rows[0].symbol),
        "market": market,
        "starting_capital_inr": STARTING_CAPITAL_INR,
        "starting_capital_usdt": starting_usdt,
        "equity_usdt": equity_usdt,
        "equity_inr": equity_usdt * rate,
        "realized_pnl_usdt": realized_pnl_usdt,
        "realized_pnl_inr": realized_pnl_usdt * rate,
        "unrealized_pnl_usdt": unrealized_pnl_usdt,
        "unrealized_pnl_inr": unrealized_pnl_usdt * rate,
        "open_position": bool(open_rows),
        "open_positions_count": len(open_rows),
        "open_trade_id": open_rows[0].id if open_rows else None,
        "open_trade_ids": [trade.id for trade in open_rows],
        "open_symbols": [trade.symbol for trade in open_rows],
        "current_price_usdt": current_price_value,
        "closed_count": closed_count,
        "wins": wins,
        "losses": losses,
        "flat": flat,
        "win_rate": win_rate,
    }


def _already_traded_this_signal(symbol: str, signal_as_of: str) -> bool:
    latest_trade = PaperTrade.objects.filter(symbol=symbol).order_by("-opened_at").first()
    if not latest_trade:
        return False
    return latest_trade.signal_snapshot.get("as_of") == signal_as_of


def _open_trade(
    *,
    symbol: str,
    action: str,
    current_price: float,
    prediction_confidence: float,
    signal_snapshot: dict[str, Any],
    model_version: str,
    risk_fraction: float,
    stop_loss_pct: float,
    take_profit_pct: float,
    market: str = DEFAULT_MARKET,
    current_prices: Mapping[str, float] | None = None,
    usd_inr: float | None = None,
) -> PaperTrade | None:
    snapshot = portfolio_snapshot(
        current_prices=current_prices,
        usd_inr=usd_inr,
        market=market,
    )
    equity_usdt = float(snapshot["equity_usdt"])
    notional_usdt = equity_usdt * risk_fraction
    if notional_usdt <= 5.0 or current_price <= 0:
        return None
    quantity = notional_usdt / current_price
    stop_loss_price = current_price * (1.0 + stop_loss_pct)
    take_profit_price = current_price * (1.0 - take_profit_pct)
    return PaperTrade.objects.create(
        symbol=symbol,
        action=action,
        quantity=quantity,
        entry_price_usdt=current_price,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
        confidence=prediction_confidence,
        signal_snapshot=signal_snapshot,
        model_version=model_version,
        notes="opened by paper engine",
    )


def rank_short_candidates(
    *,
    models: Mapping[str, SignalModel],
    symbols: list[str],
    interval: str,
    market: str,
    min_confidence: float,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for symbol in symbols:
        model = models.get(symbol)
        if model is None:
            continue
        snapshot = load_market_snapshot(symbol=symbol, interval=interval, market=market)
        prediction = model.predict(snapshot)
        candidate = {
            "symbol": symbol,
            "snapshot": snapshot,
            "prediction": prediction,
        }
        if prediction.label == "SELL" and prediction.confidence >= min_confidence:
            candidates.append(candidate)
    candidates.sort(key=lambda item: item["prediction"].confidence, reverse=True)
    return candidates


def _close_trade(trade: PaperTrade, *, current_price: float, reason: str) -> PaperTrade:
    trade.exit_price_usdt = current_price
    trade.pnl_usdt = trade_pnl_usdt(trade, current_price)
    trade.pnl_pct = _trade_pnl_pct(trade, current_price)
    if trade.pnl_usdt > 0:
        trade.outcome = PaperTrade.Outcome.WIN
    elif trade.pnl_usdt < 0:
        trade.outcome = PaperTrade.Outcome.LOSS
    else:
        trade.outcome = PaperTrade.Outcome.FLAT
    trade.closed_at = timezone.now()
    trade.notes = reason[:255]
    trade.save(
        update_fields=[
            "exit_price_usdt",
            "pnl_usdt",
            "pnl_pct",
            "outcome",
            "closed_at",
            "notes",
        ]
    )
    return trade


def run_once(
    *,
    symbol: str = DEFAULT_SYMBOL,
    symbols: list[str] | tuple[str, ...] | None = None,
    universe: int = DEFAULT_UNIVERSE,
    interval: str = DEFAULT_INTERVAL,
    market: str = DEFAULT_MARKET,
    risk_fraction: float = 0.05,
    stop_loss_pct: float = 0.15,
    take_profit_pct: float = 0.08,
    min_confidence: float = 0.55,
    usd_inr: float | None = None,
) -> dict[str, Any]:
    watchlist = tracked_symbols(symbols=symbols or ([symbol] if symbol else None), universe=universe)
    models = {
        sym: model
        for sym in watchlist
        if (model := SignalModel.load_if_available(symbol=sym)) is not None
    }
    if not models:
        raise RuntimeError("No per-symbol trained models found. Run `python manage.py seed_model` first.")
    current_prices: dict[str, float] = {}
    closed_trades: list[PaperTrade] = []
    held_trades: list[dict[str, Any]] = []
    for trade in open_trades():
        signal_snapshot = load_market_snapshot(symbol=trade.symbol, interval=interval, market=market)
        current_price = float(signal_snapshot["close"])
        current_prices[trade.symbol] = current_price
        symbol_model = models.get(trade.symbol) or SignalModel.load_if_available(symbol=trade.symbol)
        prediction = symbol_model.predict(signal_snapshot) if symbol_model is not None else None
        if current_price >= float(trade.stop_loss_price or 0.0):
            closed_trades.append(_close_trade(trade, current_price=current_price, reason="short stop loss hit"))
            continue
        if current_price <= float(trade.take_profit_price or 0.0):
            closed_trades.append(_close_trade(trade, current_price=current_price, reason="short take profit hit"))
            continue
        if prediction is not None and prediction.label != "SELL" and prediction.confidence >= min_confidence:
            closed_trades.append(_close_trade(trade, current_price=current_price, reason="bearish signal faded"))
            continue
        held_trades.append(
            {
                "trade_id": trade.id,
                "symbol": trade.symbol,
                "prediction": prediction.label if prediction is not None else "UNKNOWN",
                "confidence": prediction.confidence if prediction is not None else 0.0,
            }
        )

    open_symbols = set(PaperTrade.objects.filter(outcome=PaperTrade.Outcome.OPEN).values_list("symbol", flat=True))

    candidates = rank_short_candidates(
        models=models,
        symbols=watchlist,
        interval=interval,
        market=market,
        min_confidence=min_confidence,
    )
    opened_trades: list[PaperTrade] = []
    skipped_symbols: list[dict[str, str]] = []
    for candidate in candidates:
        signal_snapshot = candidate["snapshot"]
        prediction = candidate["prediction"]
        candidate_symbol = candidate["symbol"]
        current_price = float(signal_snapshot["close"])
        current_prices[candidate_symbol] = current_price
        if candidate_symbol in open_symbols:
            skipped_symbols.append({"symbol": candidate_symbol, "reason": "already_open"})
            continue
        if _already_traded_this_signal(candidate_symbol, str(signal_snapshot["as_of"])):
            skipped_symbols.append({"symbol": candidate_symbol, "reason": "already_traded_this_bar"})
            continue

        trade = _open_trade(
            symbol=candidate_symbol,
            action=PaperTrade.Action.SELL,
            current_price=current_price,
            prediction_confidence=prediction.confidence,
            signal_snapshot={
                "strategy_mode": "short_only_futures",
                **signal_snapshot,
                "prediction": prediction.label,
                "confidence": prediction.confidence,
                "probabilities": prediction.probabilities,
            },
            model_version=prediction.model_version,
            market=market,
            risk_fraction=risk_fraction,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            current_prices=current_prices,
            usd_inr=usd_inr,
        )
        if trade is None:
            skipped_symbols.append({"symbol": candidate_symbol, "reason": "insufficient_notional"})
            continue
        opened_trades.append(trade)
        open_symbols.add(candidate_symbol)

    if not closed_trades and not opened_trades and not held_trades:
        return {"event": "skip", "reason": "no_short_candidate", "scanned_symbols": len(watchlist)}
    return {
        "event": "batch",
        "scanned_symbols": len(watchlist),
        "opened_count": len(opened_trades),
        "opened_symbols": [trade.symbol for trade in opened_trades],
        "closed_count": len(closed_trades),
        "closed_symbols": [trade.symbol for trade in closed_trades],
        "held_count": len(held_trades),
        "held_symbols": [item["symbol"] for item in held_trades],
        "skipped": skipped_symbols,
    }


def run_forever(
    *,
    symbol: str = DEFAULT_SYMBOL,
    symbols: list[str] | tuple[str, ...] | None = None,
    universe: int = DEFAULT_UNIVERSE,
    interval: str = DEFAULT_INTERVAL,
    market: str = DEFAULT_MARKET,
    sleep_seconds: int = 60,
    risk_fraction: float = 0.05,
    stop_loss_pct: float = 0.15,
    take_profit_pct: float = 0.08,
    min_confidence: float = 0.55,
    usd_inr: float | None = None,
) -> None:
    while True:
        run_once(
            symbol=symbol,
            symbols=symbols,
            universe=universe,
            interval=interval,
            market=market,
            risk_fraction=risk_fraction,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            min_confidence=min_confidence,
            usd_inr=usd_inr,
        )
        time.sleep(max(5, int(sleep_seconds)))
