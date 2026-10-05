from django.contrib import admin
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import include, path

from apps.dashboard.views import HomeView, health

urlpatterns = [
    path("w/<uuid:workspace_id>/prospecting/", include("apps.prospecting.urls")),
    path("w/<uuid:workspace_id>/jobs/", include("apps.jobs.urls")),
    path("w/<uuid:workspace_id>/", include("common.urls")),
    path("admin/", admin.site.urls),
    path("accounts/login/", LoginView.as_view(), name="login"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
    path("health/", health, name="health"),
    path("", HomeView.as_view(), name="home"),
]
