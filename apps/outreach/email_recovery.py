"""Explicit reconciliation while the local worker is stopped; no transport calls."""

import hashlib
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.services import record_event
from apps.compliance.services import evaluate_message
from apps.jobs.locking import WorkerLock
from apps.jobs.models import Job
from common.access import workspace_access
from integrations.common.contracts import SendMessageRequest
from integrations.email.smtp import message_id

from .models import OutreachMessage
from .selectors import message_for_user
from .services import finish_domain, lock_settings, record_smtp_receipt


def recover_email(
    *,
    user: User,
    workspace_id: UUID,
    pk: UUID,
    action: str,
    evidence: str,
    confirmed: bool,
    provider_message_id: str = "",
) -> OutreachMessage:
    """Record verified server outcome or retry a confirmed failure, never an uncertain one."""
    workspace_access(user=user, workspace_id=workspace_id, delete=True)
    if confirmed is not True or not evidence.strip() or len(evidence) > 500:
        raise ValidationError("Confirma la comprobación y aporta su referencia.")
    with WorkerLock(), transaction.atomic():
        workspace = workspace_access(user=user, workspace_id=workspace_id, delete=True)
        message = message_for_user(user=user, workspace_id=workspace_id, pk=pk)
        if message.provider not in {"smtp", "cloud"} or not message.job_id:
            raise ValidationError("Solo se reconcilian mensajes reales con trabajo asociado.")
        job = Job.objects.get(pk=message.job_id, workspace_id=workspace_id)
        if (
            action == "sync"
            and message.status in {"SENT", "DELIVERED", "READ", "REPLIED"}
            and job.status in {"FAILED", "SUCCEEDED"}
        ):
            finish_domain(
                message, user, simulated=False, contacted_at=message.sent_at, proposal_actor=user
            )
            record_event(actor=user, obj=message, action="MESSAGE_STATE_RECONCILED")
            return message
        if job.status != "FAILED":
            raise ValidationError("Detén el worker y espera a que el trabajo esté fallido.")
        config = lock_settings(workspace, user)
        if action == "accepted" and message.status == "UNKNOWN":
            receipt = message_id(
                SendMessageRequest(
                    workspace_id,
                    message.idempotency_key,
                    message.recipient,
                    message.body_text,
                    sender_email=message.sender_email,
                )
            )
            if message.provider == "cloud":
                from .whatsapp_events import record_cloud_receipt, valid_provider_id

                if not valid_provider_id(provider_message_id):
                    raise ValidationError("Indica el WAMID confirmado en Meta.")
                receipt = provider_message_id
                record_cloud_receipt(message, user, receipt)
            else:
                record_smtp_receipt(message, user, receipt)
            try:
                with transaction.atomic():
                    finish_domain(
                        message,
                        user,
                        simulated=False,
                        contacted_at=message.sent_at,
                        proposal_actor=user,
                    )
            except ValidationError:
                record_event(
                    actor=user, obj=message, action="MESSAGE_STATE_RECONCILIATION_REQUIRED"
                )
            Job.objects.filter(pk=job.pk, workspace_id=workspace_id).update(
                status="SUCCEEDED",
                last_error="",
                finished_at=timezone.now(),
                result={"message_id": str(pk), "receipt": receipt, "reconciled": True},
            )
        elif (
            action == "not_accepted"
            and message.status == "UNKNOWN"
            and not message.provider_message_id
        ) or (
            action == "retry"
            and message.status == "FAILED"
            and not message.transport_started_at
            and not message.provider_message_id
        ):
            message.status = "FAILED"
            decision = evaluate_message(message, user)
            if decision.decision != "ELIGIBLE" or config.is_paused:
                raise ValidationError("La política actual no permite reintentar este mensaje.")
            if job.attempts >= 10:
                raise ValidationError("Se alcanzó el límite de recuperaciones de este trabajo.")
            OutreachMessage.objects.filter(pk=pk, workspace_id=workspace_id).update(
                status="QUEUED", transport_started_at=None, error_code="", error_message=""
            )
            Job.objects.filter(pk=job.pk, workspace_id=workspace_id).update(
                status="PENDING",
                max_attempts=max(job.max_attempts, job.attempts + 1),
                available_at=timezone.now(),
                started_at=None,
                finished_at=None,
                last_error="",
                created_by=user,
                run_token=None,
            )
        else:
            raise ValidationError(
                "Acción incompatible con el estado; no se reenvía sin reconciliación."
            )
        record_event(
            actor=user,
            obj=message,
            action="WHATSAPP_DELIVERY_RECONCILED"
            if message.provider == "cloud"
            else "EMAIL_DELIVERY_RECONCILED",
            metadata={
                "outcome": action,
                "evidence_sha256": hashlib.sha256(evidence.encode()).hexdigest(),
            },
        )
        message.refresh_from_db()
        return message
