from django.urls import path

from .website_views import EnrichmentView

app_name = "enrichment"
urlpatterns = [path("<uuid:pk>/", EnrichmentView.as_view(), name="company")]
