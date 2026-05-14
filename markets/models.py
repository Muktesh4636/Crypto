from django.db import models


class NewsArticle(models.Model):
    """News headline stored from RSS (or future sources). Deduped by URL."""

    url = models.TextField(unique=True)
    title = models.CharField(max_length=512)
    summary = models.TextField(blank=True)
    topic_slug = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        help_text="Guardian/other thematic bucket (fed_rates, geopolitics, …).",
    )
    source_feed = models.CharField(max_length=255, db_index=True)
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Parsed from RSS when present; otherwise null.",
    )
    first_ingested_at = models.DateTimeField(auto_now_add=True)
    last_ingested_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_at", "-first_ingested_at"]
        indexes = [
            models.Index(fields=["-published_at"]),
        ]

    def __str__(self) -> str:
        return self.title[:80]


class MacroObservation(models.Model):
    """Official macro time series points (default: St. Louis Fed FRED API)."""

    provider = models.CharField(max_length=16, default="fred")
    series_id = models.CharField(max_length=32, db_index=True)
    series_title = models.CharField(max_length=255, blank=True)
    observation_date = models.DateField(db_index=True)
    value = models.FloatField(null=True, blank=True)
    raw_value = models.CharField(max_length=32, blank=True)

    class Meta:
        ordering = ["-observation_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "series_id", "observation_date"],
                name="uniq_macro_observation",
            ),
        ]
        indexes = [
            models.Index(fields=["series_id", "-observation_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.series_id} @{self.observation_date}"


class PaperTrade(models.Model):
    """Paper-trading position journal used for analytics and retraining."""

    class Action(models.TextChoices):
        BUY = "BUY", "Buy"
        SELL = "SELL", "Sell"

    class Outcome(models.TextChoices):
        OPEN = "OPEN", "Open"
        WIN = "WIN", "Win"
        LOSS = "LOSS", "Loss"
        FLAT = "FLAT", "Flat"

    symbol = models.CharField(max_length=24, db_index=True, default="BTCUSDT")
    action = models.CharField(max_length=4, choices=Action.choices)
    outcome = models.CharField(
        max_length=8,
        choices=Outcome.choices,
        default=Outcome.OPEN,
        db_index=True,
    )
    quantity = models.FloatField()
    entry_price_usdt = models.FloatField()
    exit_price_usdt = models.FloatField(null=True, blank=True)
    stop_loss_price = models.FloatField(null=True, blank=True)
    take_profit_price = models.FloatField(null=True, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    pnl_usdt = models.FloatField(default=0.0)
    pnl_pct = models.FloatField(default=0.0)
    signal_snapshot = models.JSONField(default=dict, blank=True)
    model_version = models.CharField(max_length=64, blank=True, default="")
    notes = models.CharField(max_length=255, blank=True, default="")
    opened_at = models.DateTimeField(auto_now_add=True, db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-opened_at"]
        indexes = [
            models.Index(fields=["symbol", "-opened_at"]),
            models.Index(fields=["outcome", "-opened_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.symbol} {self.action} {self.outcome} @{self.entry_price_usdt}"


class FuturesFlowSnapshot(models.Model):
    """Stored Binance futures flow, positioning, and participation snapshot per symbol/minute."""

    provider = models.CharField(max_length=32, default="binance_futures", db_index=True)
    symbol = models.CharField(max_length=24, db_index=True)
    bucket_time = models.DateTimeField(db_index=True)
    observed_at = models.DateTimeField(db_index=True)
    ratio_period = models.CharField(max_length=8, default="5m")
    kline_interval = models.CharField(max_length=8, default="1h")

    last_price = models.FloatField(null=True, blank=True)
    mark_price = models.FloatField(null=True, blank=True)
    index_price = models.FloatField(null=True, blank=True)
    price_change_pct_24h = models.FloatField(null=True, blank=True)
    volume_base_24h = models.FloatField(null=True, blank=True)
    quote_volume_24h = models.FloatField(null=True, blank=True)
    trade_count_24h = models.IntegerField(null=True, blank=True)

    open_interest_contracts = models.FloatField(null=True, blank=True)
    open_interest_value_usdt = models.FloatField(null=True, blank=True)
    last_funding_rate = models.FloatField(null=True, blank=True)
    next_funding_time = models.DateTimeField(null=True, blank=True)

    global_long_short_ratio = models.FloatField(null=True, blank=True)
    global_long_account_ratio = models.FloatField(null=True, blank=True)
    global_short_account_ratio = models.FloatField(null=True, blank=True)
    top_trader_long_short_account_ratio = models.FloatField(null=True, blank=True)
    top_trader_long_account_ratio = models.FloatField(null=True, blank=True)
    top_trader_short_account_ratio = models.FloatField(null=True, blank=True)
    top_trader_long_short_position_ratio = models.FloatField(null=True, blank=True)
    top_trader_long_position_ratio = models.FloatField(null=True, blank=True)
    top_trader_short_position_ratio = models.FloatField(null=True, blank=True)

    taker_buy_sell_ratio = models.FloatField(null=True, blank=True)
    taker_buy_volume = models.FloatField(null=True, blank=True)
    taker_sell_volume = models.FloatField(null=True, blank=True)

    recent_bar_open_time = models.DateTimeField(null=True, blank=True)
    recent_bar_close_time = models.DateTimeField(null=True, blank=True)
    recent_bar_quote_volume = models.FloatField(null=True, blank=True)
    recent_bar_trade_count = models.IntegerField(null=True, blank=True)
    recent_bar_taker_buy_quote_volume = models.FloatField(null=True, blank=True)
    recent_bar_taker_sell_quote_volume = models.FloatField(null=True, blank=True)
    recent_bar_taker_buy_ratio = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["-bucket_time", "symbol"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "symbol", "bucket_time"],
                name="uniq_futures_flow_snapshot",
            ),
        ]
        indexes = [
            models.Index(fields=["symbol", "-bucket_time"]),
            models.Index(fields=["provider", "-bucket_time"]),
        ]

    def __str__(self) -> str:
        return f"{self.symbol} flow @{self.bucket_time.isoformat()}"
