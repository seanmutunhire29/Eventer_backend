from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("api.urls")),
    path("api/admin/", include("admin_api.urls")),
    path(
        "admin-frontend/",
        TemplateView.as_view(template_name="admin-frontend/index.html"),
        name="admin-frontend",
    ),
    path(
        "admin-frontend/<path:path>",
        TemplateView.as_view(template_name="admin-frontend/index.html"),
    ),
]
