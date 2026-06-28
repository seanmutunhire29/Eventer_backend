from datetime import timedelta

import django_filters
from django.utils import timezone

from core.models import Event


class EventFilter(django_filters.FilterSet):
    date = django_filters.DateFilter(field_name="start_time", lookup_expr="date")
    category = django_filters.CharFilter(method="filter_category")
    days = django_filters.NumberFilter(method="filter_days")
    since = django_filters.IsoDateTimeFilter(field_name="updated_at", lookup_expr="gte")

    class Meta:
        model = Event
        fields = ["date", "category", "days", "since"]

    def filter_category(self, queryset, name, value):
        normalized = Event.CATEGORY_ALIASES.get(value.lower(), value)
        return queryset.filter(category=normalized)

    def filter_days(self, queryset, name, value):
        start = timezone.now()
        end = start + timedelta(days=int(value))
        return queryset.filter(start_time__gte=start, start_time__lte=end)
