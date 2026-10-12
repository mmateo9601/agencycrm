from django.urls import path

from .views import RuleEditView, RulesView, ScoreView

app_name = "scoring"
urlpatterns = [
    path("rules/", RulesView.as_view(), name="rules"),
    path("rules/<uuid:pk>/", RuleEditView.as_view(), name="rule"),
    path("leads/<uuid:pk>/", ScoreView.as_view(), name="lead"),
]
