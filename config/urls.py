from django.contrib import admin
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from apps.dashboard.views import HomeView, health

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", LoginView.as_view(), name="login"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
    path("health/", health, name="health"),
    path("", HomeView.as_view(), name="home"),
]
