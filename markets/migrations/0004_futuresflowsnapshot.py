from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("markets", "0003_papertrade"),
    ]

    operations = [
        migrations.CreateModel(
            name="FuturesFlowSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider", models.CharField(db_index=True, default="binance_futures", max_length=32)),
                ("symbol", models.CharField(db_index=True, max_length=24)),
                ("bucket_time", models.DateTimeField(db_index=True)),
                ("observed_at", models.DateTimeField(db_index=True)),
                ("ratio_period", models.CharField(default="5m", max_length=8)),
                ("kline_interval", models.CharField(default="1h", max_length=8)),
                ("last_price", models.FloatField(blank=True, null=True)),
                ("mark_price", models.FloatField(blank=True, null=True)),
                ("index_price", models.FloatField(blank=True, null=True)),
                ("price_change_pct_24h", models.FloatField(blank=True, null=True)),
                ("volume_base_24h", models.FloatField(blank=True, null=True)),
                ("quote_volume_24h", models.FloatField(blank=True, null=True)),
                ("trade_count_24h", models.IntegerField(blank=True, null=True)),
                ("open_interest_contracts", models.FloatField(blank=True, null=True)),
                ("open_interest_value_usdt", models.FloatField(blank=True, null=True)),
                ("last_funding_rate", models.FloatField(blank=True, null=True)),
                ("next_funding_time", models.DateTimeField(blank=True, null=True)),
                ("global_long_short_ratio", models.FloatField(blank=True, null=True)),
                ("global_long_account_ratio", models.FloatField(blank=True, null=True)),
                ("global_short_account_ratio", models.FloatField(blank=True, null=True)),
                ("top_trader_long_short_account_ratio", models.FloatField(blank=True, null=True)),
                ("top_trader_long_account_ratio", models.FloatField(blank=True, null=True)),
                ("top_trader_short_account_ratio", models.FloatField(blank=True, null=True)),
                ("top_trader_long_short_position_ratio", models.FloatField(blank=True, null=True)),
                ("top_trader_long_position_ratio", models.FloatField(blank=True, null=True)),
                ("top_trader_short_position_ratio", models.FloatField(blank=True, null=True)),
                ("taker_buy_sell_ratio", models.FloatField(blank=True, null=True)),
                ("taker_buy_volume", models.FloatField(blank=True, null=True)),
                ("taker_sell_volume", models.FloatField(blank=True, null=True)),
                ("recent_bar_open_time", models.DateTimeField(blank=True, null=True)),
                ("recent_bar_close_time", models.DateTimeField(blank=True, null=True)),
                ("recent_bar_quote_volume", models.FloatField(blank=True, null=True)),
                ("recent_bar_trade_count", models.IntegerField(blank=True, null=True)),
                ("recent_bar_taker_buy_quote_volume", models.FloatField(blank=True, null=True)),
                ("recent_bar_taker_sell_quote_volume", models.FloatField(blank=True, null=True)),
                ("recent_bar_taker_buy_ratio", models.FloatField(blank=True, null=True)),
            ],
            options={
                "ordering": ["-bucket_time", "symbol"],
                "indexes": [
                    models.Index(fields=["symbol", "-bucket_time"], name="markets_fut_symbol_402ae7_idx"),
                    models.Index(fields=["provider", "-bucket_time"], name="markets_fut_provide_99497f_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("provider", "symbol", "bucket_time"),
                        name="uniq_futures_flow_snapshot",
                    ),
                ],
            },
        ),
    ]
