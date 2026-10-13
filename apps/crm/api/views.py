from rest_framework.mixins import ListModelMixin, RetrieveModelMixin

from apps.crm.models import Lead
from apps.crm.selectors import crm_records
from common.api import WorkspaceAPIView
from common.filters import RecordFilterForm
from common.selectors import scoped_record

from .serializers import LeadSerializer


class LeadListView(ListModelMixin, WorkspaceAPIView):
    serializer_class = LeadSerializer
    supports_filters = True

    def get_queryset(self):
        form = RecordFilterForm(self.request.query_params, model=Lead, workspace=self.workspace)
        return crm_records(
            model=Lead,
            user=self.request.user,
            workspace_id=self.workspace.pk,
            filters=self.filtered_data(form),
        )

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


class LeadDetailView(RetrieveModelMixin, WorkspaceAPIView):
    serializer_class = LeadSerializer

    def get_object(self):
        return scoped_record(
            model=Lead,
            user=self.request.user,
            workspace_id=self.workspace.pk,
            pk=self.kwargs["pk"],
        )

    def get(self, request, *args, **kwargs):
        return self.retrieve(request, *args, **kwargs)
