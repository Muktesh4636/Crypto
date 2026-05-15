from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("markets", "0005_rename_futuresflowsnapshot_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="futuresflowsnapshot",
            name="order_book_ask_depth_usdt",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="futuresflowsnapshot",
            name="order_book_bid_depth_usdt",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="futuresflowsnapshot",
            name="order_book_bid_share",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="futuresflowsnapshot",
            name="order_book_imbalance",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="futuresflowsnapshot",
            name="order_book_spread_pct",
            field=models.FloatField(blank=True, null=True),
        ),
    ]
