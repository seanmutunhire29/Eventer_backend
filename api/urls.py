from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import BuildingViewSet, EventViewSet, categories_list

router = DefaultRouter()
router.register("events", EventViewSet, basename="event")
router.register("buildings", BuildingViewSet, basename="building")

urlpatterns = [
    path("categories/", categories_list, name="categories-list"),
    path("", include(router.urls)),
]
