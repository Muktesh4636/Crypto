from __future__ import annotations

import time
from datetime import datetime, timezone as dt_timezone

import requests
from django.core.management.base import BaseCommand
from django.utils import timezone

from markets.models import FuturesFlowSnapshot
from markets.services.binance import (
    eligible_usdt_futures_symbols,
    fetch_all_futures_mark_prices,
    fetch_futures_global_long_short_ratio,
    fetch_futures_open_interest,
    fetch_futures_order_book_depth,
    fetch_futures_taker_buy_sell_ratio,
    fetch_futures_ticker_rows,
    fetch_futures_top_long_short_account_ratio,
    fetch_futures_top_long_short_position_ratio,
    fetch_latest_closed_kline,
)


def _dt_from_ms(value: int | float | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromtimestamp(float(value) / 1000.0, tz=dt_timezone.utc)


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


class Command(BaseCommand):
    help = (
        "Collect Binance USDT futures flow snapshots into PostgreSQL in controlled batches "
        "(traded amount, buy/sell pressure, funding, open interest, and long/short ratios)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--symbol", type=str, default="")
        parser.add_argument("--symbols", type=str, default="")
        parser.add_argument(
            "--universe",
            type=int,
            default=0,
            help="How many futures symbols to track. 0 means all eligible Binance USDT futures symbols.",
        )
        parser.add_argument("--batch-size", type=int, default=15)
        parser.add_argument("--ratio-period", type=str, default="5m")
        parser.add_argument("--kline-interval", type=str, default="1h")
        parser.add_argument("--sleep-seconds", type=int, default=60)
        parser.add_argument("--symbol-pause-ms", type=int, default=150)
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        symbol = options["symbol"].strip().upper()
        raw_symbols = options["symbols"].strip()
        universe = int(options["universe"])
        batch_size = max(1, min(int(options["batch_size"]), 100))
        ratio_period = options["ratio_period"].strip()
        kline_interval = options["kline_interval"].strip()
        sleep_seconds = max(10, int(options["sleep_seconds"]))
        symbol_pause_sec = max(0, int(options["symbol_pause_ms"])) / 1000.0

        explicit_symbols = [item.strip().upper() for item in raw_symbols.split(",") if item.strip()]
        if symbol:
            explicit_symbols = [symbol]

        cursor = 0
        while True:
            try:
                ticker_rows = fetch_futures_ticker_rows(limit=None)
                ticker_map = {row["symbol"]: row for row in ticker_rows}
                premium_map = fetch_all_futures_mark_prices()

                if explicit_symbols:
                    watchlist = explicit_symbols
                elif universe > 0:
                    watchlist = [row["symbol"] for row in ticker_rows[:universe]]
                else:
                    watchlist = [row["symbol"] for row in ticker_rows] or sorted(eligible_usdt_futures_symbols())

                if not watchlist:
                    raise RuntimeError("No Binance USDT futures symbols available for flow collection.")

                start = cursor % len(watchlist)
                end = start + batch_size
                if end <= len(watchlist):
                    batch_symbols = watchlist[start:end]
                else:
                    batch_symbols = watchlist[start:] + watchlist[: end - len(watchlist)]
                cursor = (cursor + batch_size) % len(watchlist)

                bucket_time = timezone.now().replace(second=0, microsecond=0)
                saved = 0
                errors: list[str] = []

                for sym in batch_symbols:
                    ticker = ticker_map.get(sym, {})
                    premium = premium_map.get(sym, {})

                    def safe_fetch(label: str, fn):
                        try:
                            return fn(), None
                        except (requests.RequestException, ValueError, KeyError) as exc:
                            return {}, f"{sym} {label}: {exc}"

                    open_interest, err = safe_fetch("open_interest", lambda: fetch_futures_open_interest(sym))
                    if err:
                        errors.append(err)
                    global_ratio, err = safe_fetch(
                        "global_ratio",
                        lambda: fetch_futures_global_long_short_ratio(sym, period=ratio_period),
                    )
                    if err:
                        errors.append(err)
                    top_account_ratio, err = safe_fetch(
                        "top_account_ratio",
                        lambda: fetch_futures_top_long_short_account_ratio(sym, period=ratio_period),
                    )
                    if err:
                        errors.append(err)
                    top_position_ratio, err = safe_fetch(
                        "top_position_ratio",
                        lambda: fetch_futures_top_long_short_position_ratio(sym, period=ratio_period),
                    )
                    if err:
                        errors.append(err)
                    taker_ratio, err = safe_fetch(
                        "taker_ratio",
                        lambda: fetch_futures_taker_buy_sell_ratio(sym, period=ratio_period),
                    )
                    if err:
                        errors.append(err)
                    recent_bar, err = safe_fetch(
                        "recent_bar",
                        lambda: fetch_latest_closed_kline(sym, interval=kline_interval, market="futures"),
                    )
                    if err:
                        errors.append(err)
                    order_book, err = safe_fetch(
                        "order_book",
                        lambda: fetch_futures_order_book_depth(sym),
                    )
                    if err:
                        errors.append(err)

                    mark_price = premium.get("mark_price")
                    open_interest_contracts = open_interest.get("open_interest")
                    open_interest_value_usdt = None
                    if open_interest_contracts is not None and mark_price is not None:
                        open_interest_value_usdt = float(open_interest_contracts) * float(mark_price)

                    recent_bar_quote_volume = _to_float(recent_bar.get("quote_volume"))
                    recent_bar_taker_buy_quote_volume = _to_float(recent_bar.get("taker_buy_quote_volume"))
                    recent_bar_taker_sell_quote_volume = None
                    if recent_bar_quote_volume is not None and recent_bar_taker_buy_quote_volume is not None:
                        recent_bar_taker_sell_quote_volume = max(
                            recent_bar_quote_volume - recent_bar_taker_buy_quote_volume,
                            0.0,
                        )
                    recent_bar_taker_buy_ratio = None
                    if recent_bar_quote_volume and recent_bar_taker_buy_quote_volume is not None:
                        recent_bar_taker_buy_ratio = recent_bar_taker_buy_quote_volume / recent_bar_quote_volume

                    FuturesFlowSnapshot.objects.update_or_create(
                        provider="binance_futures",
                        symbol=sym,
                        bucket_time=bucket_time,
                        defaults={
                            "observed_at": timezone.now(),
                            "ratio_period": ratio_period,
                            "kline_interval": kline_interval,
                            "last_price": _to_float(ticker.get("last_price")),
                            "mark_price": mark_price,
                            "index_price": premium.get("index_price"),
                            "price_change_pct_24h": _to_float(ticker.get("price_change_percent")),
                            "volume_base_24h": _to_float(ticker.get("volume")),
                            "quote_volume_24h": _to_float(ticker.get("quote_volume")),
                            "trade_count_24h": _to_int(ticker.get("count")),
                            "open_interest_contracts": open_interest_contracts,
                            "open_interest_value_usdt": open_interest_value_usdt,
                            "last_funding_rate": premium.get("last_funding_rate"),
                            "next_funding_time": _dt_from_ms(premium.get("next_funding_time")),
                            "global_long_short_ratio": global_ratio.get("long_short_ratio"),
                            "global_long_account_ratio": global_ratio.get("long_account_ratio"),
                            "global_short_account_ratio": global_ratio.get("short_account_ratio"),
                            "top_trader_long_short_account_ratio": top_account_ratio.get("long_short_ratio"),
                            "top_trader_long_account_ratio": top_account_ratio.get("long_account_ratio"),
                            "top_trader_short_account_ratio": top_account_ratio.get("short_account_ratio"),
                            "top_trader_long_short_position_ratio": top_position_ratio.get("long_short_ratio"),
                            "top_trader_long_position_ratio": top_position_ratio.get("long_position_ratio"),
                            "top_trader_short_position_ratio": top_position_ratio.get("short_position_ratio"),
                            "taker_buy_sell_ratio": taker_ratio.get("buy_sell_ratio"),
                            "taker_buy_volume": taker_ratio.get("buy_volume"),
                            "taker_sell_volume": taker_ratio.get("sell_volume"),
                            "recent_bar_open_time": _dt_from_ms(_to_int(recent_bar.get("open_time"))),
                            "recent_bar_close_time": _dt_from_ms(_to_int(recent_bar.get("close_time"))),
                            "recent_bar_quote_volume": recent_bar_quote_volume,
                            "recent_bar_trade_count": _to_int(recent_bar.get("trade_count")),
                            "recent_bar_taker_buy_quote_volume": recent_bar_taker_buy_quote_volume,
                            "recent_bar_taker_sell_quote_volume": recent_bar_taker_sell_quote_volume,
                            "recent_bar_taker_buy_ratio": recent_bar_taker_buy_ratio,
                            "order_book_bid_depth_usdt": _to_float(order_book.get("bid_depth_usdt")),
                            "order_book_ask_depth_usdt": _to_float(order_book.get("ask_depth_usdt")),
                            "order_book_imbalance": _to_float(order_book.get("order_book_imbalance")),
                            "order_book_bid_share": _to_float(order_book.get("order_book_bid_share")),
                            "order_book_spread_pct": _to_float(order_book.get("order_book_spread_pct")),
                        },
                    )
                    saved += 1
                    if symbol_pause_sec:
                        time.sleep(symbol_pause_sec)

                self.stdout.write(
                    self.style.SUCCESS(
                        f"Saved {saved} futures flow snapshots at {bucket_time.isoformat()} "
                        f"(batch {start + 1}-{min(start + len(batch_symbols), len(watchlist))} of {len(watchlist)} symbols)."
                    )
                )
                for row in errors[:20]:
                    self.stdout.write(self.style.WARNING("  " + row))
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"futures flow collection failed: {exc}"))
                if options["once"]:
                    return

            if options["once"]:
                return
            time.sleep(sleep_seconds)
