from rest_framework import serializers


class BinanceTickerSerializer(serializers.Serializer):
    symbol = serializers.CharField()
    base_asset = serializers.CharField()
    quote_asset = serializers.CharField()
    last_price = serializers.CharField()
    last_price_inr = serializers.FloatField(allow_null=True, required=False)
    price_change_percent = serializers.CharField()
    open_price = serializers.CharField()
    high_price = serializers.CharField()
    low_price = serializers.CharField()
    volume = serializers.CharField()
    quote_volume = serializers.FloatField()
    weighted_avg_price = serializers.CharField()
    open_time = serializers.IntegerField()
    close_time = serializers.IntegerField()
    count = serializers.IntegerField()
