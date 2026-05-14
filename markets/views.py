from collections import defaultdict
from datetime import date, timedelta

import requests
from django.views.generic import TemplateView
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from .ml.model import available_model_symbols, load_all_model_metadata, load_model_metadata
from .models import NewsArticle, PaperTrade
from .serializers import BinanceTickerSerializer
from .services.analysis import movers_by_daily_change_pct
from .services.binance import (
    eligible_usdt_spot_symbols,
    fetch_top_coins_by_quote_volume,
    top_futures_symbols_by_quote_volume,
)
from .services.fx import enrich_rows_inr, get_usd_inr_rate
from .services.news_rss import get_cached_world_news_sample
from .trading.paper_engine import latest_open_trade, load_market_snapshot, portfolio_snapshot, trade_pnl_usdt


def _news_item_json(item: dict) -> dict:
    """Serialize RSS dict for JSON (datetimes → ISO)."""
    out = {**item}
    pa = out.get("published_at")
    if pa is not None and hasattr(pa, "isoformat"):
        out["published_at"] = pa.isoformat()
    return out


class DashboardView(TemplateView):
    template_name = "dashboard.html"


class TradingReportsPageView(TemplateView):
    template_name = "trading_reports.html"


class HealthView(APIView):
    """Cheap liveness probe (no outbound HTTP)."""

    def get(self, request):
        return Response({"status": "ok"})


class EligibleSymbolsView(APIView):
    """Spot USDT trading symbols from exchange info (cached on server)."""

    def get(self, request):
        try:
            symbols = sorted(eligible_usdt_spot_symbols())
        except requests.RequestException as exc:
            return Response(
                {"detail": "Failed to reach Binance API", "error": str(exc)},
                status=502,
            )
        return Response({"count": len(symbols), "symbols": symbols})


class UsdInrFxView(APIView):
    """USD→INR snapshot for INR column (matches enrich_rows_inr source)."""

    def get(self, request):
        try:
            rate = get_usd_inr_rate()
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            return Response(
                {"detail": "FX provider failed", "error": str(exc)},
                status=502,
            )
        return Response(
            {
                "usd_inr": rate,
                "basis": "USDT last × USD→INR (open.er-api). USDT assumed ≈ 1 USD.",
            }
        )


class WorldNewsSampleView(APIView):
    """
    Cached merge of several **public RSS feeds** — not exhaustive global coverage.
    For every outlet / firehose-grade news you need licensed providers (Reuters, Bloomberg, etc.).
    """

    def get(self, request):
        items, statuses = get_cached_world_news_sample()
        return Response(
            {
                "coverage": (
                    "Sample RSS only (~hundreds of outlets exist). "
                    "This is world + business + crypto desks, not literally every headline on Earth."
                ),
                "feeds_health": statuses,
                "count": len(items),
                "items": [_news_item_json(x) for x in items],
            }
        )


class StoredNewsListView(APIView):
    """Headlines already saved in SQLite (see `ingest_news` management command)."""

    def get(self, request):
        try:
            limit = int(request.query_params.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 200))
        try:
            offset = int(request.query_params.get("offset", 0))
        except (TypeError, ValueError):
            offset = 0
        offset = max(0, offset)

        qs = NewsArticle.objects.all()
        topic = request.query_params.get("topic", "").strip()
        if topic:
            qs = qs.filter(topic_slug=topic)
        total = qs.count()
        qs_slice = qs[offset : offset + limit]
        results = [
            {
                "url": row.url,
                "title": row.title,
                "summary": row.summary,
                "topic_slug": row.topic_slug,
                "source_feed": row.source_feed,
                "published_at": row.published_at.isoformat() if row.published_at else None,
                "first_ingested_at": row.first_ingested_at.isoformat(),
                "last_ingested_at": row.last_ingested_at.isoformat(),
            }
            for row in qs_slice
        ]
        return Response(
            {
                "stored_total": total,
                "topic_filter": topic or None,
                "limit": limit,
                "offset": offset,
                "results": results,
            }
        )


class MacroFredView(APIView):
    """Read back FRED observations stored by `ingest_fred_macro`."""

    def get(self, request):
        raw = request.query_params.get("series", "DFF,CPIAUCSL,DCOILWTICO")
        series_ids = [s.strip().upper() for s in raw.split(",") if s.strip()][:25]
        if not series_ids:
            return Response({"detail": "Provide ?series=DFF,CPIAUCSL"}, status=400)
        try:
            limit = int(request.query_params.get("limit", 2000))
        except (TypeError, ValueError):
            limit = 2000
        limit = max(50, min(limit, 20000))
        date_from = request.query_params.get("from", "").strip()
        date_to = request.query_params.get("to", "").strip()
        qs = MacroObservation.objects.filter(provider="fred", series_id__in=series_ids)
        if date_from:
            qs = qs.filter(observation_date__gte=date_from)
        if date_to:
            qs = qs.filter(observation_date__lte=date_to)
        qs = qs.order_by("series_id", "-observation_date")[:limit]
        by_series: dict[str, list[dict]] = {sid: [] for sid in series_ids}
        for row in qs:
            by_series.setdefault(row.series_id, []).append(
                {
                    "date": row.observation_date.isoformat(),
                    "value": row.value,
                    "raw_value": row.raw_value,
                    "title": row.series_title,
                }
            )
        return Response(
            {
                "provider": "fred",
                "series_requested": series_ids,
                "points_returned": sum(len(v) for v in by_series.values()),
                "series": by_series,
            }
        )


class PriceMoversView(APIView):
    """
    Largest |24h %| movers among Binance spot USDT markets (within top-N by volume universe).
    Use this as a coarse “things are jumping today” lens — no tick replay here.
    """

    def get(self, request):
        try:
            thr = float(request.query_params.get("threshold_pct", "3"))
        except (TypeError, ValueError):
            thr = 3.0
        thr = max(0.0, min(thr, 50.0))
        try:
            universe = int(request.query_params.get("universe", "200"))
        except (TypeError, ValueError):
            universe = 200
        universe = max(50, min(universe, 500))
        try:
            max_out = min(int(request.query_params.get("max", "80")), 200)
        except (TypeError, ValueError):
            max_out = 80
        max_out = max(1, max_out)

        try:
            rows = fetch_top_coins_by_quote_volume(limit=universe)
            movers = movers_by_daily_change_pct(
                rows, min_abs_change_pct=thr, max_results=max_out
            )
            movers, fx_meta = enrich_rows_inr(movers)
        except requests.RequestException as exc:
            return Response(
                {"detail": "Failed to reach Binance or FX", "error": str(exc)},
                status=502,
            )

        ser = BinanceTickerSerializer(movers, many=True)
        return Response(
            {
                "source": "binance_spot_rest_24h",
                "min_abs_pct_change": thr,
                "universe_scan": universe,
                "note": "|%| uses Binance 24h rolling ticker field, same as REST /ticker/24hr.",
                "fx": fx_meta,
                "count": len(movers),
                "results": ser.data,
            }
        )


class TopBinanceCoinsView(APIView):
    """
    Top Binance spot USDT markets by 24h quote volume (default 200).
    """

    def get(self, request):
        try:
            limit = int(request.query_params.get("limit", 200))
        except (TypeError, ValueError):
            limit = 200
        limit = max(1, min(limit, 500))

        try:
            data = fetch_top_coins_by_quote_volume(limit=limit)
            data, fx_meta = enrich_rows_inr(data)
        except requests.RequestException as exc:
            return Response(
                {"detail": "Failed to reach Binance API", "error": str(exc)},
                status=502,
            )

        ser = BinanceTickerSerializer(data, many=True)
        return Response(
            {
                "source": "binance_spot",
                "ranked_by": "quote_volume_24h_usdt",
                "fx": fx_meta,
                "count": len(data),
                "results": ser.data,
            }
        )


def _paper_trade_json(
    row: PaperTrade,
    *,
    current_prices: dict[str, float] | None,
    usd_inr: float | None,
) -> dict:
    rate = usd_inr if isinstance(usd_inr, (int, float)) and usd_inr and usd_inr > 0 else 83.0
    is_open = row.outcome == PaperTrade.Outcome.OPEN
    live_price = (current_prices or {}).get(row.symbol)
    if is_open and live_price is not None:
        pnl_usdt = trade_pnl_usdt(row, live_price)
    else:
        pnl_usdt = row.pnl_usdt
    display_price_usdt = live_price if is_open and live_price is not None else row.exit_price_usdt
    return {
        "id": row.id,
        "symbol": row.symbol,
        "action": row.action,
        "outcome": row.outcome,
        "is_open": is_open,
        "quantity": row.quantity,
        "entry_price_usdt": row.entry_price_usdt,
        "exit_price_usdt": row.exit_price_usdt,
        "display_price_usdt": display_price_usdt,
        "pnl_usdt": pnl_usdt,
        "pnl_inr": pnl_usdt * rate,
        "pnl_pct": row.pnl_pct,
        "confidence": row.confidence,
        "stop_loss_price": row.stop_loss_price,
        "take_profit_price": row.take_profit_price,
        "model_version": row.model_version,
        "notes": row.notes,
        "opened_at": row.opened_at.isoformat(),
        "closed_at": row.closed_at.isoformat() if row.closed_at else None,
    }


def _performance_period_start(dt, period: str) -> date:
    local_dt = timezone.localtime(dt)
    day = local_dt.date()
    if period == "daily":
        return day
    if period == "weekly":
        return day - timedelta(days=day.weekday())
    if period == "monthly":
        return day.replace(day=1)
    raise ValueError(f"Unknown period: {period}")


def _performance_period_label(start: date, period: str) -> str:
    if period == "daily":
        return start.isoformat()
    if period == "weekly":
        iso = start.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if period == "monthly":
        return start.strftime("%Y-%m")
    raise ValueError(f"Unknown period: {period}")


def _build_performance_rows(
    trades: list[PaperTrade],
    *,
    period: str,
    usd_inr: float | None,
) -> list[dict[str, float | int | str]]:
    rate = usd_inr if isinstance(usd_inr, (int, float)) and usd_inr and usd_inr > 0 else 83.0
    buckets: dict[date, dict[str, float | int | str]] = defaultdict(
        lambda: {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "flat": 0,
            "pnl_usdt": 0.0,
            "best_trade_usdt": float("-inf"),
            "worst_trade_usdt": float("inf"),
        }
    )
    for trade in trades:
        if trade.closed_at is None:
            continue
        key = _performance_period_start(trade.closed_at, period)
        bucket = buckets[key]
        pnl = float(trade.pnl_usdt or 0.0)
        bucket["trades"] += 1
        bucket["pnl_usdt"] += pnl
        bucket["best_trade_usdt"] = max(float(bucket["best_trade_usdt"]), pnl)
        bucket["worst_trade_usdt"] = min(float(bucket["worst_trade_usdt"]), pnl)
        if trade.outcome == PaperTrade.Outcome.WIN:
            bucket["wins"] += 1
        elif trade.outcome == PaperTrade.Outcome.LOSS:
            bucket["losses"] += 1
        else:
            bucket["flat"] += 1

    rows: list[dict[str, float | int | str]] = []
    for start in sorted(buckets.keys(), reverse=True):
        bucket = buckets[start]
        trades_count = int(bucket["trades"])
        pnl_usdt = float(bucket["pnl_usdt"])
        best_trade_usdt = float(bucket["best_trade_usdt"]) if trades_count else 0.0
        worst_trade_usdt = float(bucket["worst_trade_usdt"]) if trades_count else 0.0
        rows.append(
            {
                "period": period,
                "period_start": start.isoformat(),
                "label": _performance_period_label(start, period),
                "trades": trades_count,
                "wins": int(bucket["wins"]),
                "losses": int(bucket["losses"]),
                "flat": int(bucket["flat"]),
                "win_rate": (int(bucket["wins"]) / trades_count) if trades_count else 0.0,
                "pnl_usdt": pnl_usdt,
                "pnl_inr": pnl_usdt * rate,
                "avg_pnl_usdt": (pnl_usdt / trades_count) if trades_count else 0.0,
                "avg_pnl_inr": ((pnl_usdt * rate) / trades_count) if trades_count else 0.0,
                "best_trade_usdt": best_trade_usdt,
                "worst_trade_usdt": worst_trade_usdt,
            }
        )
    return rows


def _current_period_snapshot(
    rows: list[dict[str, float | int | str]],
    *,
    period: str,
    usd_inr: float | None,
) -> dict[str, float | int | str]:
    today = timezone.localdate()
    if period == "daily":
        current_start = today
    elif period == "weekly":
        current_start = today - timedelta(days=today.weekday())
    elif period == "monthly":
        current_start = today.replace(day=1)
    else:
        raise ValueError(f"Unknown period: {period}")
    for row in rows:
        if row["period_start"] == current_start.isoformat():
            return row
    rate = usd_inr if isinstance(usd_inr, (int, float)) and usd_inr and usd_inr > 0 else 83.0
    return {
        "period": period,
        "period_start": current_start.isoformat(),
        "label": _performance_period_label(current_start, period),
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "flat": 0,
        "win_rate": 0.0,
        "pnl_usdt": 0.0,
        "pnl_inr": 0.0 * rate,
        "avg_pnl_usdt": 0.0,
        "avg_pnl_inr": 0.0,
        "best_trade_usdt": 0.0,
        "worst_trade_usdt": 0.0,
    }


def _overall_performance_summary(closed_trades: list[PaperTrade], *, usd_inr: float | None) -> dict[str, float | int | str]:
    rate = usd_inr if isinstance(usd_inr, (int, float)) and usd_inr and usd_inr > 0 else 83.0
    pnl_values = [float(trade.pnl_usdt or 0.0) for trade in closed_trades]
    wins = sum(1 for trade in closed_trades if trade.outcome == PaperTrade.Outcome.WIN)
    losses = sum(1 for trade in closed_trades if trade.outcome == PaperTrade.Outcome.LOSS)
    flat = sum(1 for trade in closed_trades if trade.outcome == PaperTrade.Outcome.FLAT)
    realized_pnl_usdt = sum(pnl_values)
    trades_count = len(closed_trades)
    return {
        "closed_trades": trades_count,
        "wins": wins,
        "losses": losses,
        "flat": flat,
        "win_rate": (wins / trades_count) if trades_count else 0.0,
        "realized_pnl_usdt": realized_pnl_usdt,
        "realized_pnl_inr": realized_pnl_usdt * rate,
        "avg_pnl_usdt": (realized_pnl_usdt / trades_count) if trades_count else 0.0,
        "avg_pnl_inr": ((realized_pnl_usdt * rate) / trades_count) if trades_count else 0.0,
        "best_trade_usdt": max(pnl_values) if pnl_values else 0.0,
        "worst_trade_usdt": min(pnl_values) if pnl_values else 0.0,
    }


class PaperPortfolioView(APIView):
    """Summary of the paper portfolio and recent trades for the dashboard."""

    def get(self, request):
        symbol_raw = request.query_params.get("symbol", "").strip().upper()
        symbol = symbol_raw or None
        limit_raw = request.query_params.get("limit", "all").strip().lower()
        status_raw = request.query_params.get("status", "all").strip().lower()
        tracked_symbols = available_model_symbols() or top_futures_symbols_by_quote_volume(limit=20)
        open_trade = latest_open_trade(symbol=symbol)
        focus_symbol = symbol or (open_trade.symbol if open_trade else (tracked_symbols[0] if tracked_symbols else "BTCUSDT"))
        focus_model_meta = load_model_metadata(focus_symbol)
        all_model_meta = load_all_model_metadata()
        current_prices: dict[str, float] = {}
        try:
            market_snapshot = load_market_snapshot(symbol=focus_symbol)
            current_price = float(market_snapshot["close"])
            current_prices[focus_symbol] = current_price
        except (requests.RequestException, ValueError, KeyError) as exc:
            return Response({"detail": "Failed to load live market snapshot", "error": str(exc)}, status=502)
        try:
            usd_inr = get_usd_inr_rate()
        except (requests.RequestException, KeyError, TypeError, ValueError):
            usd_inr = None

        trade_qs = PaperTrade.objects.order_by("-opened_at")
        if symbol:
            trade_qs = trade_qs.filter(symbol=symbol)
        if status_raw == "open":
            trade_qs = trade_qs.filter(outcome=PaperTrade.Outcome.OPEN)
        elif status_raw == "closed":
            trade_qs = trade_qs.exclude(outcome=PaperTrade.Outcome.OPEN)
        total_trades = trade_qs.count()
        if limit_raw != "all":
            try:
                limit = max(1, min(int(limit_raw), 500))
            except (TypeError, ValueError):
                limit = 100
            trade_qs = trade_qs[:limit]
        trade_rows = list(trade_qs)
        for row in trade_rows:
            if row.outcome != PaperTrade.Outcome.OPEN or row.symbol in current_prices:
                continue
            try:
                current_prices[row.symbol] = float(load_market_snapshot(symbol=row.symbol)["close"])
            except (requests.RequestException, ValueError, KeyError):
                continue
        summary = portfolio_snapshot(
            symbol=symbol,
            current_prices=current_prices,
            usd_inr=usd_inr,
        )
        trades = [
            _paper_trade_json(row, current_prices=current_prices, usd_inr=usd_inr)
            for row in trade_rows
        ]
        return Response(
            {
                "symbol": symbol or "ALL",
                "market": {
                    "type": market_snapshot.get("market", "futures"),
                    "focus_symbol": focus_symbol,
                    "current_price_usdt": current_price,
                    "signal_as_of": market_snapshot["as_of"],
                },
                "portfolio": summary,
                "trades": trades,
                "trade_count": total_trades,
                "tracked_symbols": tracked_symbols,
                "tracked_count": len(tracked_symbols),
                "model": {
                    **focus_model_meta,
                    "symbol": focus_model_meta.get("symbol") or focus_symbol,
                    "analysis_mode": "per_symbol",
                    "tracked_symbols": tracked_symbols,
                    "tracked_count": len(tracked_symbols),
                    "models_available": sorted(all_model_meta.keys()),
                },
            }
        )


class PerformanceReportView(APIView):
    """Closed-trade performance summaries for decision-making."""

    def get(self, request):
        symbol_raw = request.query_params.get("symbol", "").strip().upper()
        symbol = symbol_raw or None
        try:
            usd_inr = get_usd_inr_rate()
        except (requests.RequestException, KeyError, TypeError, ValueError):
            usd_inr = None

        closed_qs = PaperTrade.objects.exclude(outcome=PaperTrade.Outcome.OPEN).order_by("-closed_at", "-opened_at")
        open_qs = PaperTrade.objects.filter(outcome=PaperTrade.Outcome.OPEN).order_by("-opened_at")
        if symbol:
            closed_qs = closed_qs.filter(symbol=symbol)
            open_qs = open_qs.filter(symbol=symbol)

        closed_trades = list(closed_qs)
        daily_rows = _build_performance_rows(closed_trades, period="daily", usd_inr=usd_inr)
        weekly_rows = _build_performance_rows(closed_trades, period="weekly", usd_inr=usd_inr)
        monthly_rows = _build_performance_rows(closed_trades, period="monthly", usd_inr=usd_inr)

        return Response(
            {
                "symbol": symbol or "ALL",
                "overview": {
                    **_overall_performance_summary(closed_trades, usd_inr=usd_inr),
                    "open_trades": open_qs.count(),
                    "total_trades": len(closed_trades) + open_qs.count(),
                },
                "current": {
                    "daily": _current_period_snapshot(daily_rows, period="daily", usd_inr=usd_inr),
                    "weekly": _current_period_snapshot(weekly_rows, period="weekly", usd_inr=usd_inr),
                    "monthly": _current_period_snapshot(monthly_rows, period="monthly", usd_inr=usd_inr),
                },
                "periods": {
                    "daily": daily_rows,
                    "weekly": weekly_rows,
                    "monthly": monthly_rows,
                },
            }
        )
