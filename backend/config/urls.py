from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [path("", include("api.urls"))]
if getattr(settings, "ADMIN_ENABLED", True):
    urlpatterns.insert(0, path("admin/", admin.site.urls))
