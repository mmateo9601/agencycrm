from django.urls import path

from .views import ApproveMessageView, EnqueueMessageView

urlpatterns = [
    path("<uuid:pk>/approve/", ApproveMessageView.as_view(), name="approve-message"),
    path("<uuid:pk>/enqueue/", EnqueueMessageView.as_view(), name="enqueue-message"),
]
