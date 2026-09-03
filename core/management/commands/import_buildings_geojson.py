import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import Building

DEFAULT_GEOJSON_PATH = (
    settings.BASE_DIR.parent / "Eventer_frontend" / "assets" / "geojson" / "dartmouth-buildings.json"
)


def polygon_centroid(geometry):
    """Simple average-of-vertices centroid (not area-weighted) — good enough
    for building-sized footprints without pulling in a geometry library."""
    if geometry["type"] == "Polygon":
        rings = geometry["coordinates"]
    elif geometry["type"] == "MultiPolygon":
        rings = geometry["coordinates"][0]
    else:
        return None

    exterior = rings[0]
    points = exterior[:-1] if exterior[0] == exterior[-1] else exterior
    lng = sum(p[0] for p in points) / len(points)
    lat = sum(p[1] for p in points) / len(points)
    return lat, lng


class Command(BaseCommand):
    help = (
        "Import named building footprints from the frontend's "
        "dartmouth-buildings.json into the Building table, computing each "
        "building's centroid as its lat/lng. Safe to re-run — matches on "
        "geojson_id (the OSM @id) and updates name/coordinates in place."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            default=str(DEFAULT_GEOJSON_PATH),
            help="Path to the geojson file (defaults to the frontend's dartmouth-buildings.json)",
        )

    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.exists():
            raise CommandError(f"Geojson file not found: {path}")

        data = json.loads(path.read_text(encoding="utf-8"))
        features = data.get("features", [])

        created, updated, skipped = 0, 0, 0
        for feature in features:
            props = feature.get("properties", {})
            name = props.get("name")
            geojson_id = props.get("@id")
            if not name or not geojson_id:
                skipped += 1
                continue

            centroid = polygon_centroid(feature.get("geometry", {}))
            if centroid is None:
                skipped += 1
                continue
            lat, lng = centroid

            building, was_created = Building.objects.update_or_create(
                geojson_id=geojson_id,
                defaults={"official_name": name, "lat": lat, "lng": lng},
            )
            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported buildings from {path}: {created} created, {updated} updated, {skipped} skipped (unnamed/no id)."
            )
        )
