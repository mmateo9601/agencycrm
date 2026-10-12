from django.urls import path

from .views import (
    ArchiveAssetView,
    AssetsView,
    DownloadView,
    GenerateView,
    PrintView,
    ProposalDetailView,
    ProposalListView,
)

app_name = "proposals"
urlpatterns = [
    path("", ProposalListView.as_view(), name="list"),
    path("generate/", GenerateView.as_view(), name="generate"),
    path("assets/", AssetsView.as_view(), name="assets"),
    path("assets/<uuid:pk>/archive/", ArchiveAssetView.as_view(), name="archive_asset"),
    path(
        "assets/<uuid:pk>/download/", DownloadView.as_view(), {"asset": True}, name="asset_download"
    ),
    path("<uuid:pk>/", ProposalDetailView.as_view(), name="detail"),
    path("<uuid:pk>/print/", PrintView.as_view(), name="print"),
    path("<uuid:pk>/download/", DownloadView.as_view(), name="download"),
]
