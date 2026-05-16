from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from typing import Any

import pandas as pd

from markets.ml.model import SignalModel
from markets.models import PaperTrade
from markets.services.binance import fetch_historical_klines
from markets.services.features import FEATURE_COLUMNS, build_feature_frame
from markets.services.market_context import build_training_context
from markets.trading.constants import BACKTEST_NOTE_PREFIX, DEFAULT_INTERVAL, DEFAULT_MARKET
from markets.trading.pnl import starting_capital_usdt, trade_pnl_pct, trade_pnl_usdt

DEFAULT_WARMUP_BARS = 2400
PUMP_FOCUS_THRESHOLD = 0.55
DEFAULT_PUMP_MIN_CONFIDENCE = 0.48


def _parse_window_date(value: str, *, end_of_day: bool = False) -> datetime:
    from django.utils.dateparse import parse_date

    parsed = parse_date(value.strip())
    if parsed is None:
        raise ValueError(f"Invalid date: {value!r}")
    if end_of_day:
        return datetime(
            parsed.year,
            parsed.month,
            parsed.day,
            23,
            59,
            59,
            tzinfo=dt_timezone.utc,
        )
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=dt_timezone.utc)


def _row_to_snapshot(row: pd.Series, *, bar_time: pd.Timestamp) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "as_of": bar_time.isoformat(),
        "generated_at": bar_time.isoformat(),
        "strategy_mode": "short_only_futures",
        "close": float(row["close"]),
    }
    for column in FEATURE_COLUMNS:
        value = row.get(column)
        payload[column] = float(value) if value is not None and pd.notna(value) else 0.0
    return payload


def clear_backtest_trades(*, symbol: str | None = None) -> int:
    qs = PaperTrade.objects.filter(notes__startswith=BACKTEST_NOTE_PREFIX)
    if symbol:
        qs = qs.filter(symbol=symbol.upper())
    deleted, _ = qs.delete()
    return int(deleted)


def run_symbol_backtest(
    symbol: str,
    model: SignalModel,
    *,
    interval: str = DEFAULT_INTERVAL,
    market: str = DEFAULT_MARKET,
    start_ms: int,
    end_ms: int,
    min_confidence: float = 0.55,
    risk_fraction: float = 0.05,
    stop_loss_pct: float = 0.15,
    take_profit_pct: float = 0.08,
    warmup_bars: int = DEFAULT_WARMUP_BARS,
    clear_existing: bool = True,
    phase_label: str = "",
    focus_pumps: bool = True,
    pump_min_confidence: float = DEFAULT_PUMP_MIN_CONFIDENCE,
) -> dict[str, Any]:
    sym = symbol.upper()
    if clear_existing:
        clear_backtest_trades(symbol=sym)

    klines = fetch_historical_klines(
        symbol=sym,
        interval=interval,
        start_time_ms=start_ms,
        market=market,
        end_time_ms=end_ms,
    )
    if len(klines) < warmup_bars + 50:
        raise ValueError(f"{sym}: not enough klines for backtest (need {warmup_bars + 50}, got {len(klines)}).")

    btc_klines = None
    if sym != "BTCUSDT":
        btc_klines = fetch_historical_klines(
            symbol="BTCUSDT",
            interval=interval,
            start_time_ms=start_ms,
            market=market,
            end_time_ms=end_ms,
        )

    context = build_training_context(
        symbol=sym,
        interval=interval,
        start_time_ms=start_ms,
        end_time_ms=end_ms,
        btc_klines=btc_klines,
    )
    frame = build_feature_frame(klines, context=context)
    frame = frame.replace([float("inf"), float("-inf")], pd.NA).dropna(subset=list(FEATURE_COLUMNS), how="any")
    if len(frame) < warmup_bars + 10:
        raise ValueError(f"{sym}: not enough feature rows after warmup ({len(frame)}).")

    note_tag = f"{BACKTEST_NOTE_PREFIX}{phase_label}" if phase_label else BACKTEST_NOTE_PREFIX.rstrip(":")
    equity_usdt = starting_capital_usdt()
    open_trade: PaperTrade | None = None
    opened = 0
    closed = 0
    wins = 0
    losses = 0
    pump_trades = 0

    def close_position(trade: PaperTrade, *, price: float, bar_time: pd.Timestamp, reason: str) -> None:
        nonlocal equity_usdt, closed, wins, losses, open_trade
        pnl = trade_pnl_usdt(trade, price)
        equity_usdt += pnl
        trade.exit_price_usdt = price
        trade.pnl_usdt = pnl
        trade.pnl_pct = trade_pnl_pct(trade, price)
        if pnl > 0:
            trade.outcome = PaperTrade.Outcome.WIN
            wins += 1
        elif pnl < 0:
            trade.outcome = PaperTrade.Outcome.LOSS
            losses += 1
        else:
            trade.outcome = PaperTrade.Outcome.FLAT
        trade.closed_at = bar_time.to_pydatetime()
        trade.notes = f"{note_tag}:{reason}"[:255]
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
        closed += 1
        open_trade = None

    for bar_time, row in frame.iloc[warmup_bars:].iterrows():
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        snapshot = _row_to_snapshot(row, bar_time=bar_time)
        prediction = model.predict(snapshot)

        if open_trade is not None:
            stop = float(open_trade.stop_loss_price or 0.0)
            take = float(open_trade.take_profit_price or 0.0)
            if high >= stop:
                close_position(open_trade, price=stop, bar_time=bar_time, reason="stop_loss")
            elif low <= take:
                close_position(open_trade, price=take, bar_time=bar_time, reason="take_profit")
            # Strict exits only: stop-loss or take-profit (no signal-fade close).
            continue

        pump_score = float(snapshot.get("pump_manipulation_score_24", 0.0) or 0.0)
        news_hype = float(snapshot.get("news_hype_score_24", 0.0) or 0.0)
        is_pump_setup = focus_pumps and (
            pump_score >= PUMP_FOCUS_THRESHOLD or news_hype >= 0.45
        )
        required_confidence = pump_min_confidence if is_pump_setup else min_confidence

        if prediction.label != "SELL" or prediction.confidence < required_confidence:
            continue
        if focus_pumps and not is_pump_setup and pump_score < 0.25 and news_hype < 0.2:
            continue

        notional = equity_usdt * risk_fraction
        if notional <= 5.0 or close <= 0:
            continue

        quantity = notional / close
        open_trade = PaperTrade.objects.create(
            symbol=sym,
            action=PaperTrade.Action.SELL,
            quantity=quantity,
            entry_price_usdt=close,
            stop_loss_price=close * (1.0 + stop_loss_pct),
            take_profit_price=close * (1.0 - take_profit_pct),
            confidence=prediction.confidence,
            signal_snapshot={
                **snapshot,
                "prediction": prediction.label,
                "confidence": prediction.confidence,
                "probabilities": prediction.probabilities,
                "pump_setup": is_pump_setup,
                "pump_manipulation_score_24": pump_score,
                "news_hype_score_24": news_hype,
            },
            model_version=prediction.model_version,
            notes=f"{note_tag}:opened",
            opened_at=bar_time.to_pydatetime(),
        )
        opened += 1
        if is_pump_setup:
            pump_trades += 1

    if open_trade is not None:
        last_time = frame.index[-1]
        last_close = float(frame.iloc[-1]["close"])
        close_position(open_trade, price=last_close, bar_time=last_time, reason="end_of_window")

    return {
        "symbol": sym,
        "bars_simulated": int(len(frame) - warmup_bars),
        "trades_opened": opened,
        "trades_closed": closed,
        "wins": wins,
        "losses": losses,
        "pump_focus_trades": pump_trades,
        "ending_equity_usdt": round(equity_usdt, 4),
    }
