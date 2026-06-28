from rest_framework import serializers

from core.models import Building, BuildingAlias, Event, ScrapeSource


class AdminBuildingAliasSerializer(serializers.ModelSerializer):
    class Meta:
        model = BuildingAlias
        fields = ["id", "alias", "source"]


class AdminBuildingSerializer(serializers.ModelSerializer):
    aliases = AdminBuildingAliasSerializer(many=True, read_only=True)

    class Meta:
        model = Building
        fields = ["id", "official_name", "lat", "lng", "geojson_id", "aliases"]


class AdminEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = [
            "id",
            "event_name",
            "building",
            "unresolved_location",
            "start_time",
            "end_time",
            "description",
            "category",
            "other_info",
            "source_url",
            "scrape_source",
            "created_at",
            "updated_at",
            "is_active",
            "is_verified",
            "missed_scrape_count",
        ]


class AdminScrapeSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScrapeSource
        fields = [
            "id",
            "url",
            "label",
            "is_active",
            "scrape_interval_hours",
            "last_scraped_at",
            "last_scrape_status",
            "last_scrape_log",
            "selector_config",
        ]
        read_only_fields = ["last_scraped_at", "last_scrape_status", "last_scrape_log"]
