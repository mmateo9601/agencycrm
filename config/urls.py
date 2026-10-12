from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.urls import include, path

from apps.accounts.views import ApplicationLoginView
from apps.dashboard.views import DashboardView, HomeView, health
from apps.outreach.whatsapp_webhooks import WhatsAppWebhookView

urlpatterns = [
    path("w/<uuid:workspace_id>/dashboard/", DashboardView.as_view(), name="dashboard"),
    path("w/<uuid:workspace_id>/audit/", include("apps.audit.urls")),
    path(
        "integrations/whatsapp/<uuid:workspace_id>/webhook/",
        WhatsAppWebhookView.as_view(),
        name="whatsapp_webhook",
    ),
    path("w/<uuid:workspace_id>/outreach/", include("apps.outreach.urls")),
    path("w/<uuid:workspace_id>/proposals/", include("apps.proposals.urls")),
    path("w/<uuid:workspace_id>/scoring/", include("apps.scoring.urls")),
    path("w/<uuid:workspace_id>/enrichment/", include("apps.companies.website_urls")),
    path("w/<uuid:workspace_id>/prospecting/", include("apps.prospecting.urls")),
    path("w/<uuid:workspace_id>/jobs/", include("apps.jobs.urls")),
    path("w/<uuid:workspace_id>/", include("common.urls")),
    path("admin/", admin.site.urls),
    path("accounts/login/", ApplicationLoginView.as_view(), name="login"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
    path("health/", health, name="health"),
    path("", HomeView.as_view(), name="home"),
]
