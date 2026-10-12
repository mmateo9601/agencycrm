from django.urls import path

from .views import AuditView, ExportView, RetentionView

app_name = "audit"
urlpatterns = [
    path("", AuditView.as_view(), name="events"),
    path("export/", ExportView.as_view(), name="export"),
    path("retention/", RetentionView.as_view(), name="retention"),
]
