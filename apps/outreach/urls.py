from django.urls import path

from .views import (
    EmailConnectionView,
    ExpireSuppressionView,
    MessageView,
    OutboxView,
    PrepareView,
    SettingsView,
    SuppressionView,
    TasksView,
)

app_name = "outreach"
urlpatterns = [
    path("email/", EmailConnectionView.as_view(), name="email_connection"),
    path("", OutboxView.as_view(), name="outbox"),
    path("prepare/", PrepareView.as_view(), name="prepare"),
    path("messages/<uuid:pk>/", MessageView.as_view(), name="message"),
    path("suppression/", SuppressionView.as_view(), name="suppression"),
    path("suppression/<uuid:pk>/expire/", ExpireSuppressionView.as_view(), name="expire"),
    path("settings/", SettingsView.as_view(), name="settings"),
    path("tasks/", TasksView.as_view(), name="tasks"),
]
