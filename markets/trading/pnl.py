from __future__ import annotations

from markets.models import PaperTrade

STARTING_CAPITAL_INR = 100_000.0
DEFAULT_USD_INR = 83.0


def starting_capital_usdt(usd_inr: float | None = None) -> float:
    rate = usd_inr if isinstance(usd_inr, (int, float)) and usd_inr and usd_inr > 0 else DEFAULT_USD_INR
    return STARTING_CAPITAL_INR / float(rate)


def trade_pnl_usdt(trade: PaperTrade, price_usdt: float) -> float:
    if trade.action == PaperTrade.Action.BUY:
        return (price_usdt - trade.entry_price_usdt) * trade.quantity
    return (trade.entry_price_usdt - price_usdt) * trade.quantity


def trade_pnl_pct(trade: PaperTrade, price_usdt: float) -> float:
    if trade.entry_price_usdt == 0:
        return 0.0
    signed_move = trade_pnl_usdt(trade, price_usdt) / (trade.quantity * trade.entry_price_usdt)
    return signed_move * 100.0
