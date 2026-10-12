from django.core.exceptions import ValidationError
from django.http import HttpResponse
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from integrations.whatsapp.authentication import MetaWebhookAuthentication

from .whatsapp_events import process_webhook


class WhatsAppWebhookView(APIView):
    """Dedicated signed machine API; session-authenticated UI retains its CSRF protection."""

    authentication_classes = [MetaWebhookAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]
    http_method_names = ["get", "post"]

    def get(self, request, **kwargs):
        return HttpResponse(request.query_params["hub.challenge"], content_type="text/plain")

    def post(self, request, **kwargs):
        try:
            process_webhook(connection=request.user.connection, payload=request.data)
        except ValidationError:
            return Response({"error": "Webhook no válido."}, status=400)
        return Response({"accepted": True})
