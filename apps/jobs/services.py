from datetime import datetime
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.services import record_event
from common.access import workspace_access

from .models import Job
from .payloads import validate_payload
from .selectors import job_for_user


class JobService:
    @staticmethod
    @transaction.atomic
    def enqueue(
        *,
        user: User,
        workspace_id: UUID,
        kind: str,
        payload: dict,
        idempotency_key: str | None = None,
        priority: int = 0,
        max_attempts: int = 3,
        available_at: datetime | None = None,
    ) -> Job:
        """Persist only; duplicate keys return the existing job or reject conflicting intent."""
        workspace = workspace_access(user=user, workspace_id=workspace_id, write=True)
        payload = validate_payload(kind, payload)
        if idempotency_key is not None and not isinstance(idempotency_key, str):
            raise ValidationError("La clave de idempotencia debe ser texto.")
        key = (idempotency_key.strip() or None) if idempotency_key is not None else None
        if available_at is not None and (
            not isinstance(available_at, datetime) or timezone.is_naive(available_at)
        ):
            raise ValidationError("La fecha programada debe incluir zona horaria.")
        candidate = Job(
            workspace=workspace,
            kind=kind,
            payload=payload,
            priority=priority,
            max_attempts=max_attempts,
            idempotency_key=key,
            created_by=user,
            available_at=available_at or timezone.now(),
        )
        candidate.full_clean(validate_unique=False, validate_constraints=False)
        defaults = {
            field: getattr(candidate, field)
            for field in (
                "kind",
                "payload",
                "priority",
                "max_attempts",
                "created_by",
                "available_at",
            )
        }
        if key is None:
            job = Job.objects.create(workspace=workspace, **defaults)
            created = True
        else:
            job, created = Job.objects.get_or_create(
                workspace=workspace, idempotency_key=key, defaults=defaults
            )
        if not created:
            same_request = all(
                getattr(job, field) == defaults[field]
                for field in ("kind", "payload", "priority", "max_attempts")
            )
            # Retries change available_at; a duplicate never reschedules existing work.
            if not same_request:
                raise ValidationError("La clave de idempotencia ya identifica otro trabajo.")
            return job
        record_event(actor=user, obj=job, action="JOB_ENQUEUED")
        return job

    @staticmethod
    @transaction.atomic
    def cancel(*, user: User, workspace_id: UUID, pk: UUID) -> Job:
        """Cancel pending work only; running work must finish or be recovered after shutdown."""
        workspace_access(user=user, workspace_id=workspace_id, delete=True)
        job = job_for_user(user=user, workspace_id=workspace_id, pk=pk)
        if job.status == Job.Status.CANCELLED:
            return job
        now = timezone.now()
        if (
            Job.objects.filter(pk=pk, workspace_id=workspace_id, status=Job.Status.PENDING).update(
                status=Job.Status.CANCELLED,
                finished_at=now,
                updated_at=now,
            )
            != 1
        ):
            raise ValidationError("Solo se pueden cancelar trabajos pendientes.")
        job.refresh_from_db()
        record_event(actor=user, obj=job, action="JOB_CANCELLED")
        return job

    @staticmethod
    def recover(*, user: User, workspace_id: UUID, pk: UUID) -> dict:
        """Recover a stale job after obtaining the same lock used by the worker."""
        from .recovery import requeue_stale

        workspace_access(user=user, workspace_id=workspace_id, delete=True)
        job_for_user(user=user, workspace_id=workspace_id, pk=pk)
        return requeue_stale(workspace_id=workspace_id, job_id=pk, actor=user)
