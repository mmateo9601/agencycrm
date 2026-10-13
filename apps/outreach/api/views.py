from rest_framework.response import Response

from apps.jobs.api.serializers import JobSerializer
from apps.outreach.selectors import message_for_user
from apps.outreach.services import approve_message, enqueue_message
from common.api import EmptySerializer, MessageApprovalSerializer, WorkspaceAPIView

from .serializers import MessageSerializer


class ApproveMessageView(WorkspaceAPIView):
    serializer_class = MessageSerializer

    def post(self, request, pk, **kwargs):
        data = self.input_data(MessageApprovalSerializer)
        message = approve_message(user=request.user, workspace_id=self.workspace.pk, pk=pk, **data)
        return Response(MessageSerializer(message).data)


class EnqueueMessageView(WorkspaceAPIView):
    serializer_class = MessageSerializer

    def post(self, request, pk, **kwargs):
        self.input_data(EmptySerializer)
        job = enqueue_message(user=request.user, workspace_id=self.workspace.pk, pk=pk)
        message = message_for_user(user=request.user, workspace_id=self.workspace.pk, pk=pk)
        return Response(
            {
                "message": MessageSerializer(message).data,
                "job": JobSerializer(job).data if job else None,
            },
            status=202 if job else 200,
        )
