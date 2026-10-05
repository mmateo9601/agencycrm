from django.urls import path

from .views import JobActionView, JobCreateView, JobDetailView, JobListView, JobProgressView

app_name = "jobs"
urlpatterns = [
    path("", JobListView.as_view(), name="list"),
    path("new/", JobCreateView.as_view(), name="create"),
    path("<uuid:pk>/", JobDetailView.as_view(), name="detail"),
    path("<uuid:pk>/progress/", JobProgressView.as_view(), name="progress"),
    path("<uuid:pk>/<str:action>/", JobActionView.as_view(), name="action"),
]
