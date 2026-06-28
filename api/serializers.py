from rest_framework import serializers

from core.models import Building, BuildingAlias, Event


class BuildingAliasSerializer(serializers.ModelSerializer):
    class Meta:
        model = BuildingAlias
        fields = ["id", "alias", "source"]


class BuildingSerializer(serializers.ModelSerializer):
    aliases = BuildingAliasSerializer(many=True, read_only=True)

    class Meta:
        model = Building
        fields = ["id", "official_name", "lat", "lng", "geojson_id", "aliases"]


class EventSerializer(serializers.ModelSerializer):
    building = BuildingSerializer(read_only=True)

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
            "created_at",
            "updated_at",
            "is_active",
            "is_verified",
        ]
