from django.urls import path

from . import views

app_name = "prospecting"
urlpatterns = [
    path("", views.PlanListView.as_view(), name="list"),
    path("new/", views.PlanCreateView.as_view(), name="create"),
    path("duplicates/", views.DuplicateListView.as_view(), name="duplicates"),
    path("<uuid:pk>/", views.PlanDetailView.as_view(), name="detail"),
    path("runs/<uuid:pk>/", views.RunDetailView.as_view(), name="run"),
    path("items/<uuid:pk>/rerun/", views.RerunView.as_view(), name="rerun"),
]
