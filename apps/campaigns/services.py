import hashlib
import json
from uuid import UUID, uuid5

from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.accounts.models import ServiceOffering, User
from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from common.access import workspace_access
from common.selectors import scoped_record
from common.services import save_record

from .models import Campaign, CampaignChannel, CampaignService
from .schema import CAMPAIGN_FIELDS, CHANNEL_FIELDS


@transaction.atomic
def save_campaign(
    *,
    user: User,
    workspace_id: UUID,
    data: dict,
    pk: UUID | None = None,
    creation_id: UUID | None = None,
) -> Campaign:
    """Persist campaign and its service links atomically, checking every service ID."""
    data = dict(data)
    selected = data.pop("services", None)
    workspace_access(user=user, workspace_id=workspace_id, write=True)
    services = [
        get_object_or_404(ServiceOffering, pk=getattr(item, "pk", item), workspace_id=workspace_id)
        for item in selected or []
    ]

    def link_services(campaign: Campaign) -> None:
        if selected is None:
            return
        CampaignService.objects.filter(campaign=campaign).delete()
        for service in services:
            CampaignService.objects.get_or_create(
                workspace=campaign.workspace, campaign=campaign, service=service
            )

    return save_record(
        model=Campaign,
        user=user,
        workspace_id=workspace_id,
        data=data,
        fields=CAMPAIGN_FIELDS,
        pk=pk,
        creation_id=creation_id,
        after_save=link_services,
    )


@transaction.atomic
def create_campaign_once(
    *, user: User, workspace_id: UUID, data: dict, idempotency_key: UUID
) -> Campaign:
    """Create an API campaign with a stable UUID and retain the original request digest.

    A replay returns the current record even if an operator edited it later.
    Reusing the key for a different request is rejected; the primary key also
    prevents duplicates under concurrent creation, without schema changes.
    """
    workspace_access(user=user, workspace_id=workspace_id, write=True)
    pk = uuid5(workspace_id, f"api-campaign:{idempotency_key}")
    canonical = {**data, "services": sorted({str(value) for value in data.get("services", [])})}
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()
    existing = Campaign.objects.filter(pk=pk, workspace_id=workspace_id).first()
    if existing:
        if not AuditEvent.objects.filter(
            workspace_id=workspace_id,
            object_id=pk,
            action="API_CAMPAIGN_CREATED",
            metadata__request_digest=digest,
        ).exists():
            raise ValidationError("La clave de idempotencia identifica otra solicitud.")
        return existing
    campaign = save_campaign(user=user, workspace_id=workspace_id, data=data, creation_id=pk)
    record_event(
        actor=user,
        obj=campaign,
        action="API_CAMPAIGN_CREATED",
        metadata={"request_digest": digest},
    )
    return campaign


def save_channel(
    *, user: User, workspace_id: UUID, data: dict, pk: UUID | None = None
) -> CampaignChannel:
    """Configure a channel; this operation never sends messages or creates jobs."""
    return save_record(
        model=CampaignChannel,
        user=user,
        workspace_id=workspace_id,
        data=data,
        fields=CHANNEL_FIELDS,
        pk=pk,
        after_save=lambda channel: reconcile_followups(workspace_id, user),
    )


@transaction.atomic
def change_campaign_status(*, user: User, workspace_id: UUID, pk: UUID, target: str) -> Campaign:
    """Only preparation and archival are available before the worker phase."""
    workspace_access(user=user, workspace_id=workspace_id, write=True, delete=target == "ARCHIVED")
    campaign = scoped_record(model=Campaign, user=user, workspace_id=workspace_id, pk=pk)
    if campaign.status == target:
        return campaign
    allowed = {"DRAFT": {"READY", "ARCHIVED"}, "READY": {"DRAFT", "ARCHIVED"}}
    if target not in allowed.get(campaign.status, set()):
        raise ValidationError("Transición de campaña no permitida en esta fase.")
    changed = Campaign.objects.filter(
        pk=pk, workspace_id=workspace_id, status=campaign.status
    ).update(status=target, updated_at=timezone.now())
    if changed != 1:
        raise ValidationError("La campaña ha cambiado. Recarga la página.")
    previous = campaign.status
    campaign.refresh_from_db()
    record_event(
        actor=user,
        obj=campaign,
        action="CAMPAIGN_STATUS_CHANGED",
        metadata={"from": previous, "to": target},
    )
    reconcile_followups(workspace_id, user)
    return campaign


def reconcile_followups(workspace_id, user):
    from apps.outreach.followups import reconcile

    reconcile(workspace_id=workspace_id, actor=user)


def archive_campaign(*, user: User, workspace_id: UUID, pk: UUID) -> None:
    """Retain campaign, leads and configuration when removing it from active work."""
    change_campaign_status(user=user, workspace_id=workspace_id, pk=pk, target="ARCHIVED")
