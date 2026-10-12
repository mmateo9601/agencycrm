import hashlib
import json
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.services import record_event
from apps.crm.models import Lead, Opportunity
from apps.jobs.execution import StaleExecution
from apps.jobs.handlers import ExecutionContext
from apps.jobs.models import Job
from common.access import workspace_access
from common.selectors import scoped_record

from .engine import DEFAULTS, ENGINE_VERSION, evaluate
from .models import LeadScoreItem, ScoringRule, ScoringRun
from .selectors import input_snapshot, scoring_lead, scoring_rules


def eligible_lead(*, user: User, workspace_id: UUID, pk: UUID) -> Lead:
    workspace_access(user=user, workspace_id=workspace_id, write=True)
    lead = scoring_lead(user=user, workspace_id=workspace_id, pk=pk)
    if lead.is_archived or not lead.company.is_active or lead.campaign.status == "ARCHIVED":
        raise ValidationError("No se puntúan leads, empresas o campañas archivados.")
    return lead


@transaction.atomic
def initialize_rules(*, user: User, workspace_id: UUID) -> None:
    """Install missing defaults explicitly, without overwriting edited/disabled rules."""
    workspace = workspace_access(user=user, workspace_id=workspace_id, write=True)
    created = 0
    last_created = None
    for order, (code, name, points, configuration) in enumerate(DEFAULTS):
        rule, new = ScoringRule.objects.get_or_create(
            workspace=workspace,
            code=code,
            defaults={
                "name": name,
                "points": points,
                "configuration": configuration,
                "sort_order": order,
            },
        )
        created += int(new)
        if new:
            last_created = rule
    if created:
        record_event(
            actor=user,
            obj=last_created,
            action="SCORING_DEFAULTS_CREATED",
            metadata={"count": created},
        )


@transaction.atomic
def save_rule(
    *, user: User, workspace_id: UUID, pk: UUID, data: dict, expected_version: int
) -> ScoringRule:
    """Only workspace owners/admins may change a rule; reject stale edits."""
    workspace_access(user=user, workspace_id=workspace_id, delete=True)
    rule = scoped_record(model=ScoringRule, user=user, workspace_id=workspace_id, pk=pk)
    fields = {"name", "points", "enabled", "configuration", "sort_order"}
    if set(data) - fields or rule.version != expected_version:
        raise ValidationError("Campos no permitidos o versión antigua. Recarga la regla.")
    changed = any(getattr(rule, field) != value for field, value in data.items())
    if not changed:
        return rule
    for field, value in data.items():
        setattr(rule, field, value)
    rule.version += 1
    rule.full_clean()
    if not ScoringRule.objects.filter(
        pk=rule.pk, workspace_id=workspace_id, version=expected_version
    ).update(
        **{field: getattr(rule, field) for field in fields},
        version=rule.version,
        updated_at=timezone.now(),
    ):
        raise ValidationError("La regla ha cambiado. Recarga la página.")
    record_event(
        actor=user, obj=rule, action="SCORING_RULE_UPDATED", metadata={"version": rule.version}
    )
    return rule


@transaction.atomic
def calculate_score(
    *, user: User, workspace_id: UUID, lead_id: UUID, idempotency_key: str
) -> ScoringRun:
    """Atomically replace the active score and generated opportunities, retaining history."""
    lead = eligible_lead(user=user, workspace_id=workspace_id, pk=lead_id)
    if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key.strip()) <= 100:
        raise ValidationError("Clave de idempotencia no válida.")
    idempotency_key = idempotency_key.strip()
    previous = ScoringRun.objects.filter(
        workspace_id=workspace_id, idempotency_key=idempotency_key
    ).first()
    if previous:
        if previous.lead_id != lead.pk:
            raise ValidationError("La clave identifica otro lead.")
        return previous
    initialize_rules(user=user, workspace_id=workspace_id)
    rules = list(
        scoring_rules(user=user, workspace_id=workspace_id)
        .filter(enabled=True)
        .values("code", "name", "points", "configuration", "version", "sort_order")
    )
    snapshot = input_snapshot(lead)
    for service in snapshot["services"]:
        service["id"] = str(service["id"])
    score, items, detectors = evaluate(snapshot, rules)
    fingerprint = hashlib.sha256(
        json.dumps(
            {"engine": ENGINE_VERSION, "inputs": snapshot, "rules": rules},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    run = ScoringRun.objects.create(
        workspace_id=workspace_id,
        lead=lead,
        idempotency_key=idempotency_key,
        engine_version=ENGINE_VERSION,
        fingerprint=fingerprint,
        inputs=snapshot,
        rules={"items": rules},
        score=score,
        raw_score=sum(item["points"] for item in items),
        actor=user,
    )
    for item in items:
        LeadScoreItem.objects.create(workspace_id=workspace_id, lead=lead, scoring_run=run, **item)
    # Retired generated rows retain evidence; manual opportunities remain under manual control.
    Opportunity.objects.filter(
        workspace_id=workspace_id, lead=lead, scoring_run__isnull=False, is_active=True
    ).update(is_active=False)
    services = {row["code"]: row["id"] for row in snapshot["services"]}
    for detector in detectors:
        code = detector.pop("code")
        service_code = detector.pop("service_code")
        Opportunity.objects.create(
            workspace_id=workspace_id,
            lead=lead,
            scoring_run=run,
            detected_by=f"SCORING:{code}",
            recommended_service_id=services.get(service_code),
            **detector,
        )
    if not Lead.objects.filter(
        pk=lead.pk, workspace_id=workspace_id, current_score_run_id=lead.current_score_run_id
    ).update(
        current_score_run=run, score=score, score_version=ENGINE_VERSION, updated_at=timezone.now()
    ):
        raise ValidationError("El score ha cambiado durante el cálculo; reintenta.")
    record_event(
        actor=user,
        obj=run,
        action="SCORE_CALCULATED",
        metadata={"lead_id": str(lead.pk), "score": score, "fingerprint": fingerprint},
    )
    return run


def enqueue_score(*, user: User, workspace_id: UUID, lead_id: UUID, idempotency_key: str) -> Job:
    """Queue a local computation; the HTTP view never updates scores."""
    from apps.jobs.services import JobService

    return JobService.enqueue(
        user=user,
        workspace_id=workspace_id,
        kind=Job.Kind.SCORE_LEAD,
        payload={"lead_id": str(lead_id)},
        idempotency_key=idempotency_key,
    )


@transaction.atomic
def run_score(context: ExecutionContext) -> dict:
    """Fence worker ownership and commit the calculation together, without external I/O."""
    job = Job.objects.get(pk=context.job_id, workspace_id=context.workspace_id)
    if job.status != "RUNNING" or job.attempts != context.attempt:
        raise StaleExecution
    if not Job.objects.filter(
        pk=job.pk, run_token=job.run_token, status="RUNNING", attempts=context.attempt
    ).update(updated_at=timezone.now()):
        raise StaleExecution
    run = calculate_score(
        user=job.created_by,
        workspace_id=context.workspace_id,
        lead_id=context.payload["lead_id"],
        idempotency_key=f"job:{job.pk}",
    )
    context.progress(1, 1)
    return {"scoring_run_id": str(run.pk), "score": run.score}
