from django.contrib import admin

from .models import Building, BuildingAlias, Event, ScrapeSource


class BuildingAliasInline(admin.TabularInline):
    model = BuildingAlias
    extra = 1


@admin.register(Building)
class BuildingAdmin(admin.ModelAdmin):
    list_display = ("official_name", "geojson_id", "lat", "lng")
    inlines = [BuildingAliasInline]
    search_fields = ("official_name", "geojson_id")


@admin.register(BuildingAlias)
class BuildingAliasAdmin(admin.ModelAdmin):
    list_display = ("alias", "building", "source")
    list_filter = ("source",)
    search_fields = ("alias", "building__official_name")


@admin.register(ScrapeSource)
class ScrapeSourceAdmin(admin.ModelAdmin):
    list_display = ("label", "url", "is_active", "last_scrape_status", "last_scraped_at")
    list_filter = ("is_active", "last_scrape_status")


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = (
        "event_name",
        "building",
        "start_time",
        "category",
        "is_active",
        "is_verified",
    )
    list_filter = ("category", "is_active", "is_verified")
    search_fields = ("event_name", "description", "unresolved_location")
