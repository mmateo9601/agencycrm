from django.urls import path

from .views import LeadDetailView, LeadListView

urlpatterns = [
    path("", LeadListView.as_view(), name="leads"),
    path("<uuid:pk>/", LeadDetailView.as_view(), name="lead"),
]
