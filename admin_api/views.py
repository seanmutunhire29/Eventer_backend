from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from core.models import Building, BuildingAlias, Event, ScrapeSource
from scraper.tasks import scrape_source_task

from .serializers import (
    AdminBuildingAliasSerializer,
    AdminBuildingSerializer,
    AdminEventSerializer,
    AdminScrapeSourceSerializer,
)


class AdminEventViewSet(viewsets.ModelViewSet):
    serializer_class = AdminEventSerializer
    permission_classes = [IsAdminUser]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["category", "building", "is_active", "is_verified", "scrape_source", "review_status"]

    def get_queryset(self):
        return Event.objects.select_related("building", "scrape_source").order_by("-start_time")

    @action(detail=False, methods=["get"])
    def unresolved(self, request):
        qs = self.get_queryset().filter(unresolved_location__isnull=False).exclude(unresolved_location="")
        return Response(self.get_serializer(qs, many=True).data)

    @action(detail=False, methods=["get"])
    def pending(self, request):
        qs = self.get_queryset().filter(review_status=Event.ReviewStatus.PENDING)
        return Response(self.get_serializer(qs, many=True).data)

    @action(detail=False, methods=["post"])
    def bulk(self, request):
        action_type = request.data.get("action")
        ids = request.data.get("ids", [])
        if not ids:
            return Response({"detail": "ids required"}, status=status.HTTP_400_BAD_REQUEST)

        qs = Event.objects.filter(id__in=ids)
        if action_type == "delete":
            count = qs.count()
            qs.delete()
            return Response({"deleted": count})
        if action_type == "change_category":
            category = request.data.get("category")
            if not category:
                return Response({"detail": "category required"}, status=status.HTTP_400_BAD_REQUEST)
            updated = qs.update(category=category)
            return Response({"updated": updated})
        if action_type == "deactivate":
            updated = qs.update(is_active=False)
            return Response({"deactivated": updated})
        if action_type == "approve":
            updated = qs.update(review_status=Event.ReviewStatus.APPROVED)
            return Response({"approved": updated})
        if action_type == "reject":
            updated = qs.update(review_status=Event.ReviewStatus.REJECTED)
            return Response({"rejected": updated})
        return Response({"detail": "unknown action"}, status=status.HTTP_400_BAD_REQUEST)


class AdminBuildingViewSet(viewsets.ModelViewSet):
    queryset = Building.objects.prefetch_related("aliases")
    serializer_class = AdminBuildingSerializer
    permission_classes = [IsAdminUser]


class AdminBuildingAliasViewSet(viewsets.ModelViewSet):
    serializer_class = AdminBuildingAliasSerializer
    permission_classes = [IsAdminUser]

    def get_queryset(self):
        return BuildingAlias.objects.filter(building_id=self.kwargs["building_pk"])

    def perform_create(self, serializer):
        serializer.save(building_id=self.kwargs["building_pk"])


class AdminScrapeSourceViewSet(viewsets.ModelViewSet):
    queryset = ScrapeSource.objects.all()
    serializer_class = AdminScrapeSourceSerializer
    permission_classes = [IsAdminUser]

    @action(detail=True, methods=["post"])
    def run_now(self, request, pk=None):
        scrape_source_task.delay(int(pk))
        return Response({"detail": "Scrape queued"})

    @action(detail=True, methods=["get"])
    def logs(self, request, pk=None):
        source = self.get_object()
        return Response(
            [
                {
                    "timestamp": source.last_scraped_at,
                    "status": source.last_scrape_status,
                    "log": source.last_scrape_log,
                }
            ]
        )


@api_view(["GET"])
@permission_classes([IsAdminUser])
def alias_suggestions(request):
    unresolved = (
        Event.objects.filter(unresolved_location__isnull=False)
        .exclude(unresolved_location="")
        .values("unresolved_location")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    return Response(list(unresolved))


@api_view(["GET"])
@permission_classes([IsAdminUser])
def dashboard(request):
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = today_start + timedelta(days=7)
    day_ago = now - timedelta(hours=24)

    events_today = Event.objects.filter(
        is_active=True, start_time__date=today_start.date()
    ).count()
    events_this_week = Event.objects.filter(
        is_active=True, start_time__gte=today_start, start_time__lt=week_end
    ).count()
    by_category = list(
        Event.objects.filter(is_active=True)
        .values("category")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    sources_24h = ScrapeSource.objects.filter(last_scraped_at__gte=day_ago)
    success_count = sources_24h.filter(last_scrape_status=ScrapeSource.ScrapeStatus.SUCCESS).count()
    total_scrapes = sources_24h.count()
    scrape_health_pct = round((success_count / total_scrapes) * 100, 1) if total_scrapes else 0
    unresolved_count = (
        Event.objects.filter(unresolved_location__isnull=False)
        .exclude(unresolved_location="")
        .count()
    )
    recent_scrapes = list(
        ScrapeSource.objects.exclude(last_scraped_at__isnull=True)
        .order_by("-last_scraped_at")[:10]
        .values("id", "label", "last_scraped_at", "last_scrape_status", "last_scrape_log")
    )

    return Response(
        {
            "events_today": events_today,
            "events_this_week": events_this_week,
            "events_by_category": by_category,
            "scrape_health_pct": scrape_health_pct,
            "unresolved_location_count": unresolved_count,
            "recent_scrapes": recent_scrapes,
        }
    )
