from rest_framework.response import Response

from apps.proposals.services import transition_proposal
from common.api import RevisionSerializer, WorkspaceAPIView

from .serializers import ProposalSerializer


class ApproveProposalView(WorkspaceAPIView):
    serializer_class = ProposalSerializer

    def post(self, request, pk, **kwargs):
        data = self.input_data(RevisionSerializer)
        proposal = transition_proposal(
            user=request.user, workspace_id=self.workspace.pk, pk=pk, target="APPROVED", **data
        )
        return Response(ProposalSerializer(proposal).data)
