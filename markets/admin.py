from django.contrib import admin

from .models import MacroObservation, NewsArticle


@admin.register(NewsArticle)
class NewsArticleAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "topic_slug",
        "source_feed",
        "published_at",
        "first_ingested_at",
    )
    list_filter = ("source_feed", "topic_slug")
    search_fields = ("title", "url", "summary")
    readonly_fields = ("first_ingested_at", "last_ingested_at")
    ordering = ("-published_at", "-first_ingested_at")


@admin.register(MacroObservation)
class MacroObservationAdmin(admin.ModelAdmin):
    list_display = ("series_id", "observation_date", "value", "provider")
    list_filter = ("series_id", "provider")
    search_fields = ("series_title",)
    ordering = ("-observation_date",)
