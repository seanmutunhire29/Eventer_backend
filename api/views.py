from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet

from core.models import Building, Event

from .filters import EventFilter
from .serializers import BuildingSerializer, EventSerializer


class EventViewSet(ReadOnlyModelViewSet):
    serializer_class = EventSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = EventFilter

    def get_queryset(self):
        return (
            Event.objects.filter(is_active=True)
            .select_related("building")
            .prefetch_related("building__aliases")
        )


class BuildingViewSet(ReadOnlyModelViewSet):
    queryset = Building.objects.prefetch_related("aliases")
    serializer_class = BuildingSerializer


@api_view(["GET"])
def categories_list(request):
    data = [
        {
            "slug": choice.value,
            "label": choice.label,
            "accent_color": Event.CATEGORY_COLORS[choice],
        }
        for choice in Event.Category
    ]
    return Response(data)
