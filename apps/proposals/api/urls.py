from django.urls import path

from .views import ApproveProposalView

urlpatterns = [
    path("<uuid:pk>/approve/", ApproveProposalView.as_view(), name="approve-proposal"),
]
