from django.urls import path

from .views import RecordDeleteView, RecordDetailView, RecordEditView, RecordListView, StateView

app_name = "workspace"
urlpatterns = [
    path("<str:resource>/", RecordListView.as_view(), name="list"),
    path("<str:resource>/new/", RecordEditView.as_view(), name="create"),
    path("<str:resource>/<uuid:pk>/", RecordDetailView.as_view(), name="detail"),
    path("<str:resource>/<uuid:pk>/edit/", RecordEditView.as_view(), name="edit"),
    path("<str:resource>/<uuid:pk>/delete/", RecordDeleteView.as_view(), name="delete"),
    path("<str:resource>/<uuid:pk>/state/", StateView.as_view(), name="state"),
]
