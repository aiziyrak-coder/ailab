from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [path("", include("api.urls"))]
if getattr(settings, "ADMIN_ENABLED", True):
    # APPEND_SLASH=False: /admin → /admin/
    urlpatterns.insert(0, path("admin/", admin.site.urls))
    urlpatterns.insert(0, path("admin", RedirectView.as_view(url="/admin/", permanent=False)))
