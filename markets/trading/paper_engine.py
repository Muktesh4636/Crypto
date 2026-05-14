from __future__ import annotations

import time
from typing import Any

from django.db.models import Sum
from django.utils import timezone

from markets.ml.model import SignalModel
from markets.models import PaperTrade
from markets.services.binance import fetch_klines
from markets.services.features import latest_feature_snapshot

DEFAULT_SYMBOL = "BTCUSDT"
DEFAULT_INTERVAL = "1h"
DEFAULT_MARKET = "futures"
STARTING_CAPITAL_INR = 100_000.0
DEFAULT_USD_INR = 83.0
LOOKBACK_LIMIT = 400


def starting_capital_usdt(usd_inr: float | None = None) -> float:
    rate = usd_inr if isinstance(usd_inr, (int, float)) and usd_inr and usd_inr > 0 else DEFAULT_USD_INR
    return STARTING_CAPITAL_INR / float(rate)


def load_market_snapshot(
    symbol: str = DEFAULT_SYMBOL,
    interval: str = DEFAULT_INTERVAL,
    market: str = DEFAULT_MARKET,
) -> dict[str, Any]:
    klines = fetch_klines(symbol=symbol, interval=interval, market=market, limit=LOOKBACK_LIMIT)
    snapshot = latest_feature_snapshot(klines)
    snapshot["symbol"] = symbol
    snapshot["interval"] = interval
    snapshot["market"] = market
    return snapshot


def latest_open_trade(symbol: str = DEFAULT_SYMBOL) -> PaperTrade | None:
    return PaperTrade.objects.filter(symbol=symbol, outcome=PaperTrade.Outcome.OPEN).order_by("-opened_at").first()


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
    symbol: str = DEFAULT_SYMBOL,
    current_price: float | None = None,
    usd_inr: float | None = None,
    market: str = DEFAULT_MARKET,
) -> dict[str, Any]:
    open_trade = latest_open_trade(symbol)
    if current_price is None:
        current_price = float(load_market_snapshot(symbol=symbol, market=market)["close"])
    starting_usdt = starting_capital_usdt(usd_inr)
    closed_qs = PaperTrade.objects.filter(symbol=symbol).exclude(outcome=PaperTrade.Outcome.OPEN)
    realized_pnl_usdt = float(closed_qs.aggregate(total=Sum("pnl_usdt"))["total"] or 0.0)
    unrealized_pnl_usdt = trade_pnl_usdt(open_trade, current_price) if open_trade else 0.0
    equity_usdt = starting_usdt + realized_pnl_usdt + unrealized_pnl_usdt
    closed_count = closed_qs.count()
    wins = closed_qs.filter(outcome=PaperTrade.Outcome.WIN).count()
    losses = closed_qs.filter(outcome=PaperTrade.Outcome.LOSS).count()
    flat = closed_qs.filter(outcome=PaperTrade.Outcome.FLAT).count()
    win_rate = (wins / closed_count) if closed_count else 0.0
    rate = usd_inr if isinstance(usd_inr, (int, float)) and usd_inr and usd_inr > 0 else DEFAULT_USD_INR
    return {
        "symbol": symbol,
        "market": market,
        "starting_capital_inr": STARTING_CAPITAL_INR,
        "starting_capital_usdt": starting_usdt,
        "equity_usdt": equity_usdt,
        "equity_inr": equity_usdt * rate,
        "realized_pnl_usdt": realized_pnl_usdt,
        "realized_pnl_inr": realized_pnl_usdt * rate,
        "unrealized_pnl_usdt": unrealized_pnl_usdt,
        "unrealized_pnl_inr": unrealized_pnl_usdt * rate,
        "open_position": open_trade is not None,
        "open_trade_id": open_trade.id if open_trade else None,
        "current_price_usdt": current_price,
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
    usd_inr: float | None = None,
) -> PaperTrade | None:
    snapshot = portfolio_snapshot(symbol=symbol, current_price=current_price, usd_inr=usd_inr, market=market)
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
    interval: str = DEFAULT_INTERVAL,
    market: str = DEFAULT_MARKET,
    risk_fraction: float = 0.05,
    stop_loss_pct: float = 0.02,
    take_profit_pct: float = 0.04,
    min_confidence: float = 0.55,
    usd_inr: float | None = None,
) -> dict[str, Any]:
    model = SignalModel.load_if_available()
    if model is None:
        raise RuntimeError("No trained model found. Run `python manage.py seed_model` first.")

    signal_snapshot = load_market_snapshot(symbol=symbol, interval=interval, market=market)
    current_price = float(signal_snapshot["close"])
    prediction = model.predict(signal_snapshot)
    open_trade = latest_open_trade(symbol=symbol)

    if open_trade:
        if current_price >= float(open_trade.stop_loss_price or 0.0):
            closed = _close_trade(open_trade, current_price=current_price, reason="short stop loss hit")
            return {"event": "closed", "reason": "stop_loss", "trade_id": closed.id}
        if current_price <= float(open_trade.take_profit_price or 0.0):
            closed = _close_trade(open_trade, current_price=current_price, reason="short take profit hit")
            return {"event": "closed", "reason": "take_profit", "trade_id": closed.id}
        if prediction.label != "SELL" and prediction.confidence >= min_confidence:
            closed = _close_trade(open_trade, current_price=current_price, reason="bearish signal faded")
            return {"event": "closed", "reason": "signal_flip", "trade_id": closed.id}
        return {
            "event": "hold_open",
            "trade_id": open_trade.id,
            "prediction": prediction.label,
            "confidence": prediction.confidence,
        }

    if _already_traded_this_signal(symbol, str(signal_snapshot["as_of"])):
        return {"event": "skip", "reason": "already_traded_this_bar"}
    if prediction.confidence < min_confidence or prediction.label != "SELL":
        return {"event": "skip", "reason": "not_a_short_signal", "prediction": prediction.label}

    trade = _open_trade(
        symbol=symbol,
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
        usd_inr=usd_inr,
    )
    if trade is None:
        return {"event": "skip", "reason": "insufficient_notional"}
    return {
        "event": "opened",
        "trade_id": trade.id,
        "action": trade.action,
        "confidence": prediction.confidence,
    }


def run_forever(
    *,
    symbol: str = DEFAULT_SYMBOL,
    interval: str = DEFAULT_INTERVAL,
    market: str = DEFAULT_MARKET,
    sleep_seconds: int = 60,
    risk_fraction: float = 0.05,
    stop_loss_pct: float = 0.02,
    take_profit_pct: float = 0.04,
    min_confidence: float = 0.55,
    usd_inr: float | None = None,
) -> None:
    while True:
        run_once(
            symbol=symbol,
            interval=interval,
            market=market,
            risk_fraction=risk_fraction,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            min_confidence=min_confidence,
            usd_inr=usd_inr,
        )
        time.sleep(max(5, int(sleep_seconds)))
