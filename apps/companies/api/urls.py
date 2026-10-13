from django.urls import path

from .views import CompanyDetailView, CompanyListView

urlpatterns = [
    path("", CompanyListView.as_view(), name="companies"),
    path("<uuid:pk>/", CompanyDetailView.as_view(), name="company"),
]
