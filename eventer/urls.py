from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView

from core import views as core_views

urlpatterns = [
    path("", core_views.landing, name="landing"),
    path("download/android/", core_views.download_android, name="download-android"),
    path("download/ios/", core_views.download_ios, name="download-ios"),
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

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
