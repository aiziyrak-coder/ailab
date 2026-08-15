from django.urls import path

from . import auth_views, views

urlpatterns = [
    path("login", auth_views.LoginPageView.as_view(), name="login-page"),
    path("register", auth_views.RegisterPageView.as_view(), name="register-page"),
    path("api/auth/login", auth_views.LoginApiView.as_view(), name="api-auth-login"),
    path("api/auth/register", auth_views.RegisterApiView.as_view(), name="api-auth-register"),
    path("api/auth/logout", auth_views.LogoutApiView.as_view(), name="api-auth-logout"),
    path("api/auth/me", auth_views.MeApiView.as_view(), name="api-auth-me"),
    path("api/auth/check", auth_views.AuthCheckView.as_view(), name="api-auth-check"),
    path("", views.IndexView.as_view(), name="index"),
    path("favicon.ico", views.favicon_view, name="favicon"),
    path("video_feed", views.video_feed_view, name="video-feed"),
    path("health", views.HealthView.as_view(), name="health"),
    path("healthz", views.HealthView.as_view(), name="healthz"),
    path("api/health", views.HealthView.as_view(), name="api-health"),
    path("api/scan_cameras", views.ScanCamerasView.as_view(), name="api-scan-cameras"),
    path("api/start_camera", views.StartCameraView.as_view(), name="api-start-camera"),
    path("api/stop_camera", views.StopCameraView.as_view(), name="api-stop-camera"),
    path("api/analyze", views.AnalyzeView.as_view(), name="api-analyze"),
    path("api/analysis_result", views.AnalysisResultView.as_view(), name="api-analysis-result"),
    path("api/analyses", views.AnalysisListView.as_view(), name="api-analyses"),
    path("api/analyses/<str:public_id>", views.AnalysisDetailView.as_view(), name="api-analysis-detail"),
    path("api/patients/lookup", views.PatientLookupView.as_view(), name="api-patients-lookup"),
    path("api/capture", views.CaptureView.as_view(), name="api-capture"),
    path("api/status", views.StatusView.as_view(), name="api-status"),
]
