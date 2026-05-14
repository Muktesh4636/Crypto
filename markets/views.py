import requests
from django.views.generic import TemplateView
from rest_framework.response import Response
from rest_framework.views import APIView

from .ml.model import load_model_metadata
from .models import NewsArticle, PaperTrade
from .serializers import BinanceTickerSerializer
from .services.analysis import movers_by_daily_change_pct
from .services.binance import eligible_usdt_spot_symbols, fetch_top_coins_by_quote_volume
from .services.fx import enrich_rows_inr, get_usd_inr_rate
from .services.news_rss import get_cached_world_news_sample
from .trading.paper_engine import load_market_snapshot, portfolio_snapshot, trade_pnl_usdt


def _news_item_json(item: dict) -> dict:
    """Serialize RSS dict for JSON (datetimes → ISO)."""
    out = {**item}
    pa = out.get("published_at")
    if pa is not None and hasattr(pa, "isoformat"):
        out["published_at"] = pa.isoformat()
    return out


class DashboardView(TemplateView):
    template_name = "dashboard.html"


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


def _paper_trade_json(row: PaperTrade, *, current_price: float | None, usd_inr: float | None) -> dict:
    rate = usd_inr if isinstance(usd_inr, (int, float)) and usd_inr and usd_inr > 0 else 83.0
    if row.outcome == PaperTrade.Outcome.OPEN and current_price is not None:
        pnl_usdt = trade_pnl_usdt(row, current_price)
    else:
        pnl_usdt = row.pnl_usdt
    return {
        "id": row.id,
        "symbol": row.symbol,
        "action": row.action,
        "outcome": row.outcome,
        "quantity": row.quantity,
        "entry_price_usdt": row.entry_price_usdt,
        "exit_price_usdt": row.exit_price_usdt,
        "pnl_usdt": pnl_usdt,
        "pnl_inr": pnl_usdt * rate,
        "pnl_pct": row.pnl_pct,
        "confidence": row.confidence,
        "model_version": row.model_version,
        "notes": row.notes,
        "opened_at": row.opened_at.isoformat(),
        "closed_at": row.closed_at.isoformat() if row.closed_at else None,
    }


class PaperPortfolioView(APIView):
    """Summary of the paper portfolio and recent trades for the dashboard."""

    def get(self, request):
        symbol = request.query_params.get("symbol", "BTCUSDT").strip().upper() or "BTCUSDT"
        try:
            market_snapshot = load_market_snapshot(symbol=symbol)
            current_price = float(market_snapshot["close"])
        except (requests.RequestException, ValueError, KeyError) as exc:
            return Response({"detail": "Failed to load live market snapshot", "error": str(exc)}, status=502)
        try:
            usd_inr = get_usd_inr_rate()
        except (requests.RequestException, KeyError, TypeError, ValueError):
            usd_inr = None

        summary = portfolio_snapshot(symbol=symbol, current_price=current_price, usd_inr=usd_inr)
        trades = [
            _paper_trade_json(row, current_price=current_price, usd_inr=usd_inr)
            for row in PaperTrade.objects.filter(symbol=symbol).order_by("-opened_at")[:10]
        ]
        return Response(
            {
                "symbol": symbol,
                "market": {
                    "type": market_snapshot.get("market", "futures"),
                    "current_price_usdt": current_price,
                    "signal_as_of": market_snapshot["as_of"],
                },
                "portfolio": summary,
                "recent_trades": trades,
                "model": load_model_metadata(),
            }
        )
