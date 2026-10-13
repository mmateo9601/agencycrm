"""Session-only API transport, strict inputs and explicit workspace authorization."""

from collections.abc import Mapping

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import ValidationError
from rest_framework.generics import GenericAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer

from common.access import workspace_access


class StrictInputMixin:
    def to_internal_value(self, data):
        if isinstance(data, Mapping):
            allowed = {key for key, field in self.fields.items() if not field.read_only}
            unknown = set(data) - allowed
            if unknown:
                raise ValidationError({key: ["Campo no permitido."] for key in sorted(unknown)})
        return super().to_internal_value(data)


class StrictSerializer(StrictInputMixin, serializers.Serializer):
    pass


class WorkspaceQuerySerializer(StrictSerializer):
    workspace_id = serializers.UUIDField()


class RevisionSerializer(StrictSerializer):
    expected_revision = serializers.IntegerField(min_value=0)


class MessageApprovalSerializer(RevisionSerializer):
    policy_review_confirmed = serializers.BooleanField()


class EmptySerializer(StrictSerializer):
    pass


class InternalPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = None


class WorkspaceAPIView(GenericAPIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]
    renderer_classes = [JSONRenderer]
    pagination_class = InternalPagination
    supports_filters = False

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        values = request.query_params.getlist("workspace_id")
        if len(values) != 1:
            raise ValidationError({"workspace_id": ["Indica un único UUID de workspace."]})
        query = WorkspaceQuerySerializer(data={"workspace_id": values[0]})
        query.is_valid(raise_exception=True)
        self.workspace = workspace_access(
            user=request.user, workspace_id=query.validated_data["workspace_id"]
        )
        if not self.supports_filters or request.method not in {"GET", "HEAD"}:
            self.validate_query_parameters({"workspace_id"})

    def handle_exception(self, exc):
        if isinstance(exc, DjangoValidationError):
            exc = ValidationError(
                exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}
            )
        return super().handle_exception(exc)

    def input_data(self, serializer_class):
        serializer = serializer_class(data=self.request.data)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    def validate_query_parameters(self, allowed):
        unknown = set(self.request.query_params) - allowed
        repeated = {
            key
            for key in self.request.query_params
            if len(self.request.query_params.getlist(key)) != 1
        }
        if unknown or repeated:
            raise ValidationError(
                {key: ["Filtro no permitido o repetido."] for key in sorted(unknown | repeated)}
            )

    def filtered_data(self, form):
        self.validate_query_parameters({"workspace_id", "page", *form.fields})
        if not form.is_valid():
            raise ValidationError(dict(form.errors))
        return form.cleaned_data
