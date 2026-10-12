import io
import uuid
from dataclasses import asdict
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import F, Max
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.services import record_event
from apps.crm.models import Lead
from apps.jobs.execution import StaleExecution
from apps.jobs.handlers import ExecutionContext
from apps.jobs.models import Job
from common.access import workspace_access
from common.selectors import scoped_record
from integrations.ai.contracts import ProposalContext
from integrations.ai.mock import MockAIProvider, template_proposal

from .documents import content_hash, document_pdf, upload_bytes
from .models import Proposal, ProposalAttachment, SalesAsset
from .policies import require_approved
from .selectors import assets_for_user, proposal_for_user, proposal_snapshot


def eligible_lead(*, user: User, workspace_id: UUID, pk: UUID) -> Lead:
    """Recheck the active tenant, membership, lead, company and campaign."""
    workspace_access(user=user, workspace_id=workspace_id, write=True)
    lead = scoped_record(model=Lead, user=user, workspace_id=workspace_id, pk=pk)
    if lead.is_archived or not lead.company.is_active or lead.campaign.status == "ARCHIVED":
        raise ValidationError("No se generan o aprueban propuestas para registros archivados.")
    return lead


def authorize_payload(*, user: User, workspace_id: UUID, payload: dict) -> list[SalesAsset]:
    """Resolve all attachment IDs in the same workspace before queueing or acquisition."""
    lead = eligible_lead(user=user, workspace_id=workspace_id, pk=payload["lead_id"])
    proposal_snapshot(lead)
    if payload["provider"] == "mock" and settings.AI_PROVIDER != "mock":
        raise ValidationError("Solo está disponible IA simulada.")
    assets = []
    for pk in payload["asset_ids"]:
        asset = scoped_record(model=SalesAsset, user=user, workspace_id=workspace_id, pk=pk)
        if not asset.is_active:
            raise ValidationError("Uno de los activos seleccionados está archivado.")
        assets.append(asset)
    return assets


def enqueue_proposal(
    *,
    user: User,
    workspace_id: UUID,
    lead_id: UUID,
    idempotency_key: str,
    provider: str = "template",
    asset_ids: list[str] | None = None,
) -> Job:
    """Persist a generation job only; CPU and file generation stay in the worker."""
    from apps.jobs.services import JobService

    if asset_ids is None:
        asset_ids = [
            str(pk)
            for pk in assets_for_user(user=user, workspace_id=workspace_id)
            .filter(is_active=True, is_default=True)
            .values_list("pk", flat=True)[:6]
        ]
    return JobService.enqueue(
        user=user,
        workspace_id=workspace_id,
        kind=Job.Kind.GENERATE_PROPOSAL,
        payload={"lead_id": str(lead_id), "provider": provider, "asset_ids": asset_ids},
        idempotency_key=idempotency_key,
    )


def generate_proposal(
    *,
    user: User,
    workspace_id: UUID,
    payload: dict,
    idempotency_key: str,
    context: ExecutionContext | None = None,
) -> Proposal:
    """Build PDF outside transactions, then persist version, links and audit atomically."""
    from apps.jobs.payloads import validate_payload

    payload = validate_payload(Job.Kind.GENERATE_PROPOSAL, payload)
    if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key.strip()) <= 100:
        raise ValidationError("Clave de idempotencia no válida.")
    idempotency_key = idempotency_key.strip()
    eligible_lead(user=user, workspace_id=workspace_id, pk=payload["lead_id"])
    previous = Proposal.objects.filter(
        workspace_id=workspace_id, idempotency_key=idempotency_key
    ).first()
    if previous:
        provider = "mock" if previous.generated_by == "AI" else "template"
        attached = sorted(str(pk) for pk in previous.attachments.values_list("asset_id", flat=True))
        if (
            str(previous.lead_id) != payload["lead_id"]
            or provider != payload["provider"]
            or attached != payload["asset_ids"]
        ):
            raise ValidationError("La clave identifica otra propuesta.")
        return previous
    assets = authorize_payload(user=user, workspace_id=workspace_id, payload=payload)
    lead = eligible_lead(user=user, workspace_id=workspace_id, pk=payload["lead_id"])
    snapshot = proposal_snapshot(lead)
    maximum = lead.proposals.aggregate(value=Max("version"))["value"] or 0
    generated = (
        MockAIProvider().generate_proposal(ProposalContext(snapshot))
        if payload["provider"] == "mock"
        else template_proposal(ProposalContext(snapshot))
    )
    proposal = Proposal(
        workspace_id=workspace_id,
        lead=lead,
        version=maximum + 1,
        status="GENERATED",
        **asdict(generated),
        services_json={"items": snapshot["services"]},
        snapshot=snapshot,
        idempotency_key=idempotency_key,
        generated_by="AI" if payload["provider"] == "mock" else "TEMPLATE",
        generated_model="mock-v1" if payload["provider"] == "mock" else "",
    )
    pdf = document_pdf(proposal, snapshot)
    name = None
    try:
        with transaction.atomic():
            if context:
                fence_execution(context)
            lead = eligible_lead(user=user, workspace_id=workspace_id, pk=payload["lead_id"])
            authorize_payload(user=user, workspace_id=workspace_id, payload=payload)
            # The write locks this lead before checking and assigning the next version.
            Lead.objects.filter(pk=lead.pk, workspace_id=workspace_id).update(
                updated_at=timezone.now()
            )
            if (lead.proposals.aggregate(value=Max("version"))["value"] or 0) != maximum:
                raise ValidationError("La versión ha cambiado; encola de nuevo la generación.")
            name = default_storage.save(
                f"proposals/{workspace_id}/{proposal.pk}/{uuid.uuid4()}.pdf", ContentFile(pdf)
            )
            proposal.pdf = name
            proposal.pdf_size, proposal.pdf_sha256 = len(pdf), content_hash(pdf)
            proposal.save()
            for asset in assets:
                ProposalAttachment.objects.create(
                    workspace_id=workspace_id, proposal=proposal, asset=asset
                )
            lead.proposals.exclude(pk=proposal.pk).exclude(
                status__in=["SENT", "SUPERSEDED"]
            ).update(status="SUPERSEDED", revision=F("revision") + 1, updated_at=timezone.now())
            record_event(
                actor=user,
                obj=proposal,
                action="PROPOSAL_GENERATED",
                metadata={"version": proposal.version, "provider": payload["provider"]},
            )
        return proposal
    except Exception:
        if name:
            default_storage.delete(name)
        raise


def fence_execution(context: ExecutionContext) -> Job:
    job = Job.objects.get(pk=context.job_id, workspace_id=context.workspace_id)
    if job.status != "RUNNING" or job.attempts != context.attempt:
        raise StaleExecution
    if not Job.objects.filter(
        pk=job.pk, run_token=job.run_token, status="RUNNING", attempts=context.attempt
    ).update(updated_at=timezone.now()):
        raise StaleExecution
    return job


def run_proposal(context: ExecutionContext) -> dict:
    """Recover a committed version by the job's stable key; never duplicate files or approvals."""
    job = fence_execution(context)
    proposal = generate_proposal(
        user=job.created_by,
        workspace_id=context.workspace_id,
        payload=context.payload,
        idempotency_key=f"job:{job.pk}",
        context=context,
    )
    context.progress(1, 1)
    return {"proposal_id": str(proposal.pk), "version": proposal.version}


@transaction.atomic
def transition_proposal(
    *, user: User, workspace_id: UUID, pk: UUID, target: str, expected_revision: int
) -> Proposal:
    """Require current version, explicit review and fenced approval of immutable content."""
    proposal = proposal_for_user(user=user, workspace_id=workspace_id, pk=pk)
    eligible_lead(user=user, workspace_id=workspace_id, pk=proposal.lead_id)
    if target in {"APPROVED", "SENT"}:
        workspace_access(user=user, workspace_id=workspace_id, delete=True)
    if proposal.status == target:
        return proposal
    if proposal.revision != expected_revision:
        raise ValidationError("La propuesta ha cambiado. Recarga antes de confirmar.")
    allowed = {"GENERATED": "REVIEWED", "REVIEWED": "APPROVED", "APPROVED": "SENT"}
    if allowed.get(proposal.status) != target:
        raise ValidationError("Transición de propuesta no permitida.")
    if target == "SENT":
        require_approved(proposal)
    if proposal.lead.proposals.filter(version__gt=proposal.version).exists():
        raise ValidationError("Solo se puede aprobar o registrar la versión más reciente.")
    changes = {"status": target, "revision": F("revision") + 1, "updated_at": timezone.now()}
    if target == "REVIEWED":
        changes.update(reviewed_by=user, reviewed_at=timezone.now())
    if target == "APPROVED":
        if not proposal.reviewed_at or not proposal.reviewed_by_id:
            raise ValidationError("La propuesta requiere revisión previa.")
        private_document(user=user, workspace_id=workspace_id, pk=pk)
        for attachment in proposal.attachments.filter(workspace_id=workspace_id):
            private_document(
                user=user, workspace_id=workspace_id, pk=attachment.asset_id, asset=True
            )
        changes.update(approved_by=user, approved_at=timezone.now())
    if not Proposal.objects.filter(
        pk=pk, workspace_id=workspace_id, status=proposal.status, revision=expected_revision
    ).update(**changes):
        raise ValidationError("La propuesta ha cambiado durante la confirmación.")
    proposal.refresh_from_db()
    record_event(
        actor=user,
        obj=proposal,
        action=f"PROPOSAL_{target}",
        metadata={"version": proposal.version},
    )
    return proposal


def upload_asset(
    *, user: User, workspace_id: UUID, upload, name: str, asset_type: str, is_default: bool = False
) -> SalesAsset:
    """Validate privately uploaded PDF bytes and compensate storage on database failure."""
    workspace_access(user=user, workspace_id=workspace_id, delete=True)
    data = upload_bytes(upload)
    asset = SalesAsset(
        workspace_id=workspace_id,
        name=name,
        asset_type=asset_type,
        is_default=is_default,
        size_bytes=len(data),
        sha256=content_hash(data),
    )
    filename = None
    try:
        filename = default_storage.save(
            f"sales_assets/{workspace_id}/{asset.pk}/{uuid.uuid4()}.pdf", ContentFile(data)
        )
        asset.file = filename
        with transaction.atomic():
            workspace_access(user=user, workspace_id=workspace_id, delete=True)
            asset.save()
            record_event(actor=user, obj=asset, action="SALES_ASSET_UPLOADED")
        return asset
    except Exception:
        if filename:
            default_storage.delete(filename)
        raise


@transaction.atomic
def archive_asset(*, user: User, workspace_id: UUID, pk: UUID) -> None:
    workspace_access(user=user, workspace_id=workspace_id, delete=True)
    asset = scoped_record(model=SalesAsset, user=user, workspace_id=workspace_id, pk=pk)
    if asset.is_active:
        SalesAsset.objects.filter(pk=pk, workspace_id=workspace_id).update(
            is_active=False, updated_at=timezone.now()
        )
        record_event(actor=user, obj=asset, action="SALES_ASSET_ARCHIVED")


def private_document(
    *, user: User, workspace_id: UUID, pk: UUID, asset: bool = False
) -> io.BytesIO:
    """Authorize before opening; reject tampered paths, oversized files or hash mismatches."""
    obj = scoped_record(
        model=SalesAsset if asset else Proposal, user=user, workspace_id=workspace_id, pk=pk
    )
    field, digest = (obj.file, obj.sha256) if asset else (obj.pdf, obj.pdf_sha256)
    prefix = f"{'sales_assets' if asset else 'proposals'}/{workspace_id}/{pk}/"
    if not field.name.startswith(prefix) or ".." in field.name or "\\" in field.name:
        raise ValidationError("Archivo no disponible.")
    try:
        with field.storage.open(field.name, "rb") as handle:
            data = handle.read(2_000_001)
    except OSError as error:
        raise ValidationError("Archivo no disponible.") from error
    if len(data) > 2_000_000 or content_hash(data) != digest:
        raise ValidationError("Archivo no disponible o integridad incorrecta.")
    return io.BytesIO(data)


class ProposalService:
    generate = staticmethod(generate_proposal)
    transition = staticmethod(transition_proposal)
    enqueue = staticmethod(enqueue_proposal)
