from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.services import record_event
from apps.companies.models import Contact
from apps.compliance.policies import AUTOMATED, approval_digest, recipient_for
from apps.compliance.services import evaluate_message
from apps.crm.models import Lead
from apps.crm.services import LeadStateService
from apps.jobs.execution import StaleExecution
from apps.jobs.handlers import ExecutionContext
from apps.jobs.models import Job
from apps.proposals.models import Proposal
from apps.proposals.services import private_document, transition_proposal
from common.access import workspace_access
from common.selectors import scoped_record
from integrations.common.contracts import SendAttachment, SendMessageRequest
from integrations.common.errors import ProviderError, ProviderPermanentError
from integrations.common.factory import outbound_provider

from .models import MessageAttachment, MessageEvent, OutreachMessage, OutreachSettings, SalesTask
from .selectors import message_for_user
from .settings import ensure_settings


def event(message: OutreachMessage, kind: str, identity: str, payload: dict | None = None):
    """Append each logical event once, without bodies or recipient data in metadata."""
    return MessageEvent.objects.get_or_create(
        workspace_id=message.workspace_id,
        message=message,
        event_type=kind,
        provider_event_id=identity,
        defaults={"payload": payload or {}},
    )[0]


def lock_settings(workspace, user) -> OutreachSettings:
    config = ensure_settings(workspace, user)
    OutreachSettings.objects.filter(pk=config.pk).update(updated_at=timezone.now())
    config.refresh_from_db()
    return config


@transaction.atomic
def prepare_message(
    *,
    user: User,
    workspace_id: UUID,
    proposal_id: UUID,
    contact_id: UUID | None,
    channel: str,
    idempotency_key: str,
    subject: str = "",
    body_text: str = "",
    scheduled_at=None,
) -> OutreachMessage:
    """Prepare immutable text and protected attachment references; never approve automatically."""
    workspace = workspace_access(user=user, workspace_id=workspace_id, write=True)
    proposal = scoped_record(model=Proposal, user=user, workspace_id=workspace_id, pk=proposal_id)
    contact = (
        scoped_record(model=Contact, user=user, workspace_id=workspace_id, pk=contact_id)
        if contact_id
        else None
    )
    if proposal.lead.is_archived or not proposal.lead.company.is_active:
        raise ValidationError("El lead o empresa están archivados.")
    if contact and contact.company_id != proposal.lead.company_id:
        raise ValidationError("Contacto de otra empresa.")
    if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key.strip()) <= 100:
        raise ValidationError("Clave no válida.")
    key = idempotency_key.strip()
    existing = OutreachMessage.objects.filter(workspace=workspace, idempotency_key=key).first()
    subject = subject or proposal.title
    body_text = body_text or (proposal.executive_summary + "\n\n" + proposal.next_steps)[:12000]
    if existing:
        if (
            existing.proposal_id,
            existing.contact_id,
            existing.channel,
            existing.subject,
            existing.body_text,
        ) != (proposal.pk, contact.pk if contact else None, channel, subject, body_text):
            raise ValidationError("La clave identifica otro mensaje.")
        return existing
    ensure_settings(workspace, user)
    message = OutreachMessage.objects.create(
        workspace=workspace,
        lead=proposal.lead,
        proposal=proposal,
        contact=contact,
        channel=channel,
        idempotency_key=key,
        recipient=recipient_for(proposal.lead, contact, channel),
        subject=subject,
        body_text=body_text,
        provider="mock" if channel in AUTOMATED else "manual",
        created_by=user,
        status="PENDING_APPROVAL",
        scheduled_at=scheduled_at or timezone.now(),
    )
    MessageAttachment.objects.create(
        workspace=workspace,
        message=message,
        proposal=proposal,
        file=proposal.pdf.name,
        filename=f"proposal-{proposal.pk}-v{proposal.version}.pdf",
        size_bytes=proposal.pdf_size,
        sha256=proposal.pdf_sha256,
    )
    for link in proposal.attachments.filter(workspace=workspace).select_related("asset"):
        asset = link.asset
        MessageAttachment.objects.create(
            workspace=workspace,
            message=message,
            asset=asset,
            file=asset.file.name,
            filename=f"asset-{asset.pk}.pdf",
            size_bytes=asset.size_bytes,
            sha256=asset.sha256,
        )
    record_event(actor=user, obj=message, action="MESSAGE_PREPARED")
    return message


@transaction.atomic
def approve_message(
    *,
    user: User,
    workspace_id: UUID,
    pk: UUID,
    expected_revision: int,
    policy_review_confirmed: bool,
) -> OutreachMessage:
    """Human approval binds recipient, text, proposal and attachments by hash."""
    workspace_access(user=user, workspace_id=workspace_id, delete=True)
    message = message_for_user(user=user, workspace_id=workspace_id, pk=pk)
    if message.status == "APPROVED":
        return message
    if message.status not in {"DRAFT", "PENDING_APPROVAL"} or message.revision != expected_revision:
        raise ValidationError("Estado o revisión de mensaje no válido.")
    if policy_review_confirmed is not True:
        raise ValidationError("Confirma la revisión de la política aplicable antes de aprobar.")
    # Approval is allowed before the scheduled send time, but never before proposal approval.
    if message.proposal.status != "APPROVED":
        raise ValidationError("Aprueba primero esta versión de propuesta.")
    read_attachments(message, user)
    if not OutreachMessage.objects.filter(
        pk=pk, workspace_id=workspace_id, revision=expected_revision, status=message.status
    ).update(
        status="APPROVED",
        approved_by=user,
        approved_at=timezone.now(),
        policy_review_confirmed=True,
        approval_hash=approval_digest(message),
        revision=F("revision") + 1,
        updated_at=timezone.now(),
    ):
        raise ValidationError("El mensaje ha cambiado.")
    message.refresh_from_db()
    record_event(actor=user, obj=message, action="MESSAGE_APPROVED")
    return message


def block_message(message, user, decision) -> None:
    OutreachMessage.objects.filter(pk=message.pk, workspace_id=message.workspace_id).update(
        status="SUPPRESSED",
        error_code=",".join(decision.reasons["codes"])[:100],
        error_message="Contacto bloqueado por la política vigente.",
        updated_at=timezone.now(),
    )
    event(message, "SUPPRESSED", str(decision.pk), {"decision_id": str(decision.pk)})
    record_event(
        actor=user,
        obj=message,
        action="MESSAGE_SUPPRESSED",
        metadata={"decision_id": str(decision.pk)},
    )


@transaction.atomic
def enqueue_message(*, user: User, workspace_id: UUID, pk: UUID) -> Job | None:
    """Check current policy before queueing; a queued request never changes its job identity."""
    from apps.jobs.services import JobService

    workspace = workspace_access(user=user, workspace_id=workspace_id, write=True)
    message = message_for_user(user=user, workspace_id=workspace_id, pk=pk)
    if message.job_id:
        return message.job
    if message.status != "APPROVED":
        raise ValidationError("El mensaje debe estar aprobado antes de encolar.")
    lock_settings(workspace, user)
    # Future dates are enforced by Job.available_at; all other policy is checked now.
    decision = evaluate_message(message, user)
    codes = set(decision.reasons["codes"]) - {"NOT_DUE"}
    if codes:
        block_message(message, user, decision)
        return None
    job = JobService.enqueue(
        user=user,
        workspace_id=workspace_id,
        kind="SEND_OUTREACH",
        payload={"message_id": str(message.pk)},
        idempotency_key=f"send:{message.pk}",
        available_at=message.scheduled_at,
    )
    OutreachMessage.objects.filter(pk=pk, workspace_id=workspace_id, status="APPROVED").update(
        status="QUEUED", job=job, revision=F("revision") + 1, updated_at=timezone.now()
    )
    event(message, "QUEUED", str(job.pk))
    record_event(actor=user, obj=message, action="MESSAGE_QUEUED")
    return job


def authorize_send(*, user: User, workspace_id: UUID, payload: dict) -> OutreachMessage:
    workspace_access(user=user, workspace_id=workspace_id, write=True)
    message = message_for_user(user=user, workspace_id=workspace_id, pk=payload["message_id"])
    if (
        message.status not in {"APPROVED", "QUEUED", "SENDING", "FAILED", "SENT"}
        or not message.approved_at
    ):
        raise ValidationError("No se puede enviar un mensaje sin aprobación.")
    return message


def read_attachments(message, user) -> tuple[SendAttachment, ...]:
    items = list(message.attachments.filter(workspace_id=message.workspace_id).order_by("filename"))
    if len(items) > 6:
        raise ValidationError("Demasiados adjuntos.")
    result = []
    for item in items:
        obj = item.asset if item.asset_id else item.proposal
        data = private_document(
            user=user, workspace_id=message.workspace_id, pk=obj.pk, asset=bool(item.asset_id)
        ).getvalue()
        source_hash = obj.sha256 if item.asset_id else obj.pdf_sha256
        if source_hash != item.sha256:
            raise ValidationError("El adjunto ha cambiado desde la preparación.")
        result.append(SendAttachment(filename=item.filename, mime_type=item.mime_type, data=data))
    return tuple(result)


def owned_job(context: ExecutionContext, token=None) -> Job:
    job = Job.objects.get(pk=context.job_id, workspace_id=context.workspace_id)
    if (
        job.status != "RUNNING"
        or job.attempts != context.attempt
        or (token and job.run_token != token)
    ):
        raise StaleExecution
    if not Job.objects.filter(
        pk=job.pk, run_token=job.run_token, status="RUNNING", attempts=context.attempt
    ).update(updated_at=timezone.now()):
        raise StaleExecution
    return job


def reserve_attempt(message, user) -> None:
    OutreachMessage.objects.filter(pk=message.pk, workspace_id=message.workspace_id).update(
        status="SENDING",
        send_attempts=F("send_attempts") + 1,
        attempt_started_at=timezone.now(),
        error_code="",
        error_message="",
        updated_at=timezone.now(),
    )
    message.refresh_from_db()
    event(
        message,
        "SEND_ATTEMPT",
        f"attempt:{message.send_attempts}",
        {"simulated": message.provider == "mock"},
    )
    record_event(actor=user, obj=message, action="MESSAGE_SEND_ATTEMPT")


def finish_message(message, user, receipt: str, *, simulated: bool) -> None:
    now = timezone.now()
    OutreachMessage.objects.filter(pk=message.pk, workspace_id=message.workspace_id).update(
        status="SENT",
        provider_message_id=receipt,
        sent_at=now,
        failed_at=None,
        error_code="",
        error_message="",
        updated_at=now,
    )
    event(
        message,
        "PROVIDER_ACCEPTED" if simulated else "MANUAL_COMPLETED",
        receipt,
        {"simulated": simulated},
    )
    event(message, "SENT", receipt, {"simulated": simulated})
    lead = Lead.objects.get(pk=message.lead_id, workspace_id=message.workspace_id)
    if lead.status == "READY":
        LeadStateService.transition(
            user=user,
            workspace_id=message.workspace_id,
            pk=lead.pk,
            target="CONTACTED",
            reason="Contacto simulado" if simulated else "Acción manual confirmada",
            expected_revision=lead.status_revision,
        )
    Lead.objects.filter(pk=lead.pk, workspace_id=message.workspace_id).update(last_contact_at=now)
    proposal = Proposal.objects.get(pk=message.proposal_id, workspace_id=message.workspace_id)
    transition_proposal(
        user=message.approved_by,
        workspace_id=message.workspace_id,
        pk=proposal.pk,
        target="SENT",
        expected_revision=proposal.revision,
    )
    record_event(actor=user, obj=message, action="MESSAGE_SENT", metadata={"simulated": simulated})


def send_outreach(context: ExecutionContext) -> dict:
    """Reserve limits atomically, invoke only local mocks outside transactions, then finalize."""
    with transaction.atomic():
        job = owned_job(context)
        token = job.run_token
        workspace = workspace_access(
            user=job.created_by, workspace_id=context.workspace_id, write=True
        )
        message = authorize_send(
            user=job.created_by, workspace_id=context.workspace_id, payload=context.payload
        )
        if message.job_id != job.pk:
            raise ValidationError("Trabajo no asociado al mensaje.")
        if message.status == "SENT":
            return {"message_id": str(message.pk), "receipt": message.provider_message_id}
        lock_settings(workspace, job.created_by)
        reserved = message.status == "SENDING"
        decision = evaluate_message(message, job.created_by, reserved=reserved)
        if decision.decision != "ELIGIBLE":
            block_message(message, job.created_by, decision)
            return {"message_id": str(message.pk), "blocked": True}
        if message.channel not in AUTOMATED:
            task, _ = SalesTask.objects.get_or_create(
                workspace_id=context.workspace_id,
                message=message,
                defaults={
                    "lead": message.lead,
                    "type": "CALL" if message.channel == "PHONE" else message.channel,
                    "assigned_to": job.created_by,
                    "notes": "Acción manual; no hay automatización externa.",
                },
            )
            event(message, "MANUAL_TASK_CREATED", str(task.pk))
            record_event(actor=job.created_by, obj=task, action="MANUAL_TASK_CREATED")
            return {"task_id": str(task.pk), "manual": True}
        if not reserved:
            reserve_attempt(message, job.created_by)
    try:
        attachments = read_attachments(message, job.created_by)
        # File validation can take time; refresh policy again immediately before transport.
        with transaction.atomic():
            owned_job(context, token)
            workspace_access(user=job.created_by, workspace_id=context.workspace_id, write=True)
            message.refresh_from_db()
            decision = evaluate_message(message, job.created_by, reserved=True)
            if decision.decision != "ELIGIBLE":
                block_message(message, job.created_by, decision)
                return {"message_id": str(message.pk), "blocked": True}
        # The factory has no live implementation and rejects any non-mock configuration.
        provider = outbound_provider(message.channel)
        result = provider.send(
            SendMessageRequest(
                workspace_id=context.workspace_id,
                idempotency_key=message.idempotency_key,
                recipient=message.recipient,
                body_text=message.body_text,
                subject=message.subject,
                attachments=attachments,
            )
        )
        if not result.simulated:
            raise ProviderPermanentError
    except Exception as error:
        with transaction.atomic():
            owned_job(context, token)
            OutreachMessage.objects.filter(pk=message.pk, workspace_id=context.workspace_id).update(
                status="FAILED",
                failed_at=timezone.now(),
                error_code=type(error).__name__[:100],
                error_message=error.public_message
                if isinstance(error, ProviderError)
                else "Fallo durante la simulación.",
            )
            event(message, "FAILED", f"attempt:{message.send_attempts}")
            record_event(actor=job.created_by, obj=message, action="MESSAGE_FAILED")
        raise
    with transaction.atomic():
        owned_job(context, token)
        workspace_access(user=job.created_by, workspace_id=context.workspace_id, write=True)
        message.refresh_from_db()
        # Only mocks are used: a changed policy after the local call still prevents finalization.
        decision = evaluate_message(message, job.created_by, reserved=True)
        if decision.decision != "ELIGIBLE":
            block_message(message, job.created_by, decision)
            return {"message_id": str(message.pk), "blocked": True}
        finish_message(message, job.created_by, result.provider_message_id, simulated=True)
    return {"message_id": str(message.pk), "receipt": result.provider_message_id, "simulated": True}


@transaction.atomic
def complete_task(*, user: User, workspace_id: UUID, pk: UUID, outcome: str) -> SalesTask:
    workspace = workspace_access(user=user, workspace_id=workspace_id, write=True)
    task = scoped_record(model=SalesTask, user=user, workspace_id=workspace_id, pk=pk)
    if task.status != "PENDING":
        return task
    if not outcome.strip() or len(outcome) > 2000:
        raise ValidationError("Indica el resultado de la acción manual (máximo 2000 caracteres).")
    lock_settings(workspace, user)
    message = message_for_user(user=user, workspace_id=workspace_id, pk=task.message_id)
    decision = evaluate_message(message, user)
    if decision.decision != "ELIGIBLE":
        block_message(message, user, decision)
        SalesTask.objects.filter(pk=pk, workspace_id=workspace_id).update(status="CANCELLED")
        record_event(actor=user, obj=task, action="MANUAL_TASK_CANCELLED")
    else:
        reserve_attempt(message, user)
        finish_message(message, user, f"manual:{task.pk}", simulated=False)
        SalesTask.objects.filter(pk=pk, workspace_id=workspace_id).update(
            status="COMPLETED", outcome=outcome
        )
        record_event(actor=user, obj=task, action="MANUAL_TASK_COMPLETED")
    task.refresh_from_db()
    return task


@transaction.atomic
def configure(
    *, user: User, workspace_id: UUID, data: dict, expected_revision: int
) -> OutreachSettings:
    workspace = workspace_access(user=user, workspace_id=workspace_id, delete=True)
    config = lock_settings(workspace, user)
    allowed = {
        "daily_email_limit",
        "daily_whatsapp_limit",
        "default_cooldown_days",
        "max_attempts_per_company",
    }
    if set(data) - allowed or config.revision != expected_revision:
        raise ValidationError("Campos no permitidos o configuración antigua.")
    for field, value in data.items():
        setattr(config, field, value)
    config.revision += 1
    config.save()
    record_event(actor=user, obj=config, action="OUTREACH_LIMITS_UPDATED")
    return config


@transaction.atomic
def pause(*, user: User, workspace_id: UUID, paused: bool) -> None:
    if type(paused) is not bool:
        raise ValidationError("Estado de pausa no válido.")
    workspace = workspace_access(user=user, workspace_id=workspace_id, delete=True)
    config = lock_settings(workspace, user)
    if config.is_paused != paused:
        config.is_paused = paused
        config.revision += 1
        config.save()
        record_event(
            actor=user, obj=config, action="OUTREACH_PAUSED" if paused else "OUTREACH_RESUMED"
        )


class OutboundService:
    prepare = staticmethod(prepare_message)
    approve = staticmethod(approve_message)
    enqueue = staticmethod(enqueue_message)
    send = staticmethod(send_outreach)
