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
