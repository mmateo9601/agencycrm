from django.urls import path

from .views import JobDetailView, JobListView

urlpatterns = [
    path("", JobListView.as_view(), name="jobs"),
    path("<uuid:pk>/", JobDetailView.as_view(), name="job"),
]
