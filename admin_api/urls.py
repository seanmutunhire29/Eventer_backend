from django.urls import include, path
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.routers import DefaultRouter
from rest_framework_nested import routers

from .views import (
    AdminBuildingAliasViewSet,
    AdminBuildingViewSet,
    AdminEventViewSet,
    AdminScrapeSourceViewSet,
    alias_suggestions,
    dashboard,
)

router = DefaultRouter()
router.register("events", AdminEventViewSet, basename="admin-event")
router.register("buildings", AdminBuildingViewSet, basename="admin-building")
router.register("scrape-sources", AdminScrapeSourceViewSet, basename="admin-scrape-source")

buildings_router = routers.NestedDefaultRouter(router, "buildings", lookup="building")
buildings_router.register("aliases", AdminBuildingAliasViewSet, basename="admin-building-alias")

urlpatterns = [
    path("login/", obtain_auth_token, name="admin-login"),
    path("dashboard/", dashboard, name="admin-dashboard"),
    path("aliases/suggestions/", alias_suggestions, name="admin-alias-suggestions"),
    path("", include(router.urls)),
    path("", include(buildings_router.urls)),
]
