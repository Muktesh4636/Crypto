from django.urls import path

from .views import (
    EligibleSymbolsView,
    HealthView,
    MacroFredView,
    PaperPortfolioView,
    PerformanceReportView,
    PriceMoversView,
    StoredNewsListView,
    TopBinanceCoinsView,
    UsdInrFxView,
    WorldNewsSampleView,
)

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("coins/eligible/", EligibleSymbolsView.as_view(), name="eligible-symbols"),
    path("fx/usd-inr/", UsdInrFxView.as_view(), name="usd-inr-fx"),
    path("macro/fred/", MacroFredView.as_view(), name="macro-fred"),
    path("analysis/movers/", PriceMoversView.as_view(), name="price-movers"),
    path("news/world-sample/", WorldNewsSampleView.as_view(), name="world-news-sample"),
    path("news/stored/", StoredNewsListView.as_view(), name="news-stored"),
    path("coins/top/", TopBinanceCoinsView.as_view(), name="top-binance-coins"),
    path("trading/paper-portfolio/", PaperPortfolioView.as_view(), name="paper-portfolio"),
    path("trading/performance-report/", PerformanceReportView.as_view(), name="performance-report"),
]
