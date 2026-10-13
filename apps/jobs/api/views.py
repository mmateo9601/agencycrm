from rest_framework.mixins import ListModelMixin, RetrieveModelMixin

from apps.jobs.forms import JobFilterForm
from apps.jobs.selectors import job_for_user, jobs_for_user
from common.api import WorkspaceAPIView

from .serializers import JobSerializer


class JobListView(ListModelMixin, WorkspaceAPIView):
    serializer_class = JobSerializer
    supports_filters = True

    def get_queryset(self):
        return jobs_for_user(
            user=self.request.user,
            workspace_id=self.workspace.pk,
            filters=self.filtered_data(JobFilterForm(self.request.query_params)),
        )

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


class JobDetailView(RetrieveModelMixin, WorkspaceAPIView):
    serializer_class = JobSerializer

    def get_object(self):
        return job_for_user(
            user=self.request.user, workspace_id=self.workspace.pk, pk=self.kwargs["pk"]
        )

    def get(self, request, *args, **kwargs):
        return self.retrieve(request, *args, **kwargs)
