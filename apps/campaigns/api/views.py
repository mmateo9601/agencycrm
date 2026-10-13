from rest_framework.mixins import ListModelMixin
from rest_framework.response import Response

from apps.campaigns.models import Campaign
from apps.campaigns.selectors import campaigns_for_api
from apps.campaigns.services import create_campaign_once
from common.api import WorkspaceAPIView
from common.filters import RecordFilterForm

from .serializers import CampaignCreateSerializer, CampaignSerializer


class CampaignListView(ListModelMixin, WorkspaceAPIView):
    serializer_class = CampaignSerializer
    supports_filters = True

    def get_queryset(self):
        form = RecordFilterForm(self.request.query_params, model=Campaign, workspace=self.workspace)
        return campaigns_for_api(
            user=self.request.user,
            workspace_id=self.workspace.pk,
            filters=self.filtered_data(form),
        )

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def post(self, request, **kwargs):
        data = self.input_data(CampaignCreateSerializer)
        key = data.pop("idempotency_key")
        campaign = create_campaign_once(
            user=request.user, workspace_id=self.workspace.pk, data=data, idempotency_key=key
        )
        return Response(CampaignSerializer(campaign).data, status=201)
