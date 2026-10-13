from rest_framework.mixins import ListModelMixin, RetrieveModelMixin

from apps.companies.models import Company
from apps.companies.selectors import company_records
from common.api import WorkspaceAPIView
from common.filters import RecordFilterForm
from common.selectors import scoped_record

from .serializers import CompanySerializer


class CompanyListView(ListModelMixin, WorkspaceAPIView):
    serializer_class = CompanySerializer
    supports_filters = True

    def get_queryset(self):
        form = RecordFilterForm(self.request.query_params, model=Company, workspace=self.workspace)
        return company_records(
            model=Company,
            user=self.request.user,
            workspace_id=self.workspace.pk,
            filters=self.filtered_data(form),
        )

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


class CompanyDetailView(RetrieveModelMixin, WorkspaceAPIView):
    serializer_class = CompanySerializer

    def get_object(self):
        return scoped_record(
            model=Company,
            user=self.request.user,
            workspace_id=self.workspace.pk,
            pk=self.kwargs["pk"],
        )

    def get(self, request, *args, **kwargs):
        return self.retrieve(request, *args, **kwargs)
