from django.urls import include, path

app_name = "api"
urlpatterns = [
    path("companies/", include("apps.companies.api.urls")),
    path("leads/", include("apps.crm.api.urls")),
    path("campaigns/", include("apps.campaigns.api.urls")),
    path("jobs/", include("apps.jobs.api.urls")),
    path("proposals/", include("apps.proposals.api.urls")),
    path("messages/", include("apps.outreach.api.urls")),
]
