"""Enqueue explicitly and commit bounded observations/contacts after network I/O."""

from dataclasses import asdict
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.services import record_event
from apps.jobs.execution import StaleExecution
from apps.jobs.handlers import ExecutionContext
from apps.jobs.models import Job
from common.access import workspace_access
from common.security import crawl_url
from common.selectors import scoped_record
from integrations.websites.crawler import CrawlResult
from integrations.websites.errors import CrawlError
from integrations.websites.factory import website_provider

from .models import Company, Contact, SocialProfile, WebsiteObservation


def validate_enrichment_payload(payload: dict) -> dict:
    """Snapshot the company, URL and provider; callers cannot submit arbitrary crawl options."""
    try:
        if not isinstance(payload, dict) or set(payload) != {"company_id", "root_url", "provider"}:
            raise ValueError
        if payload["provider"] not in {"mock", "web"}:
            raise ValueError
        return {
            "company_id": str(UUID(str(payload["company_id"]))),
            "root_url": crawl_url(payload["root_url"]),
            "provider": payload["provider"],
        }
    except (ValueError, TypeError, CrawlError) as exc:
        raise ValidationError(
            "Datos de enriquecimiento no válidos; revisa la URL pública."
        ) from exc


def authorize_enrichment(*, user: User, workspace_id: UUID, payload: dict) -> Company:
    """Recheck membership, active company and fixed website before acquisition and persistence."""
    workspace_access(user=user, workspace_id=workspace_id, write=True)
    company = scoped_record(
        model=Company, user=user, workspace_id=workspace_id, pk=payload["company_id"]
    )
    try:
        valid = company.is_active and crawl_url(company.website) == payload["root_url"]
    except CrawlError:
        valid = False
    if not valid:
        raise ValidationError("La empresa está archivada o su web ha cambiado/no es válida.")
    if payload["provider"] == "web" and not (
        settings.WEBSITE_PROVIDER == "web" and settings.WEB_CRAWLER_ENABLED
    ):
        raise ValidationError("El rastreo web requiere activación explícita por entorno.")
    return company


def enqueue_enrichment(
    *, user: User, workspace_id: UUID, company_id: UUID, idempotency_key: str
) -> Job:
    """Queue enrichment only; the HTTP request never runs in the web process."""
    from apps.jobs.services import JobService

    company = scoped_record(model=Company, user=user, workspace_id=workspace_id, pk=company_id)
    return JobService.enqueue(
        user=user,
        workspace_id=workspace_id,
        kind=Job.Kind.ENRICH_COMPANY,
        payload={
            "company_id": str(company.pk),
            "root_url": company.website,
            "provider": settings.WEBSITE_PROVIDER,
        },
        idempotency_key=idempotency_key,
    )


def execution_job(context: ExecutionContext) -> Job:
    job = Job.objects.get(pk=context.job_id, workspace_id=context.workspace_id)
    if job.status != Job.Status.RUNNING or job.attempts != context.attempt:
        raise StaleExecution
    return job


def run_enrichment(context: ExecutionContext) -> dict:
    """Reuse committed success after recovery; failures remain visible per attempt."""
    job = execution_job(context)
    token = job.run_token
    company = authorize_enrichment(
        user=job.created_by, workspace_id=context.workspace_id, payload=context.payload
    )
    previous = WebsiteObservation.objects.filter(
        workspace_id=context.workspace_id, job=job, error_code=""
    ).first()
    if previous:
        return {"observation_id": str(previous.pk), "pages_crawled": previous.pages_crawled}
    failed = WebsiteObservation.objects.filter(
        workspace_id=context.workspace_id, job=job, attempt=context.attempt
    ).first()
    if failed:
        raise CrawlError(
            failed.error_code,
            retryable=failed.findings.get("retryable", False),
            retry_after=failed.findings.get("retry_after", 30),
        )
    started = timezone.now()
    error = None
    context.progress(0, 1)
    try:
        result = website_provider(context.payload["provider"], settings).crawl(
            context.payload["root_url"], company.country_code
        )
    except CrawlError as exc:
        result, error = CrawlResult(), exc
        result.findings = {"retryable": exc.retryable, "retry_after": exc.retry_after}
    with transaction.atomic():
        job = execution_job(context)
        # Fence the write against recovered/reclaimed jobs as well as revoked permissions.
        if not Job.objects.filter(
            pk=job.pk, run_token=token, status=Job.Status.RUNNING, attempts=context.attempt
        ).update(updated_at=timezone.now()):
            raise StaleExecution
        company = authorize_enrichment(
            user=job.created_by, workspace_id=context.workspace_id, payload=context.payload
        )
        observation, created = WebsiteObservation.objects.get_or_create(
            workspace_id=context.workspace_id,
            job=job,
            attempt=context.attempt,
            defaults={
                "company": company,
                "provider": context.payload["provider"],
                "root_url": context.payload["root_url"],
                "crawl_started_at": started,
                "crawl_finished_at": timezone.now(),
                **asdict(result),
                "error_code": error.code if error else "",
                "error_message": error.public_message if error else "",
            },
        )
        if created and not error:
            import_contacts(company=company, observation=observation)
        if created:
            record_event(
                actor=job.created_by,
                obj=observation,
                action="WEBSITE_ENRICHMENT_FAILED" if error else "WEBSITE_ENRICHED",
                metadata={"job_id": str(job.pk), "pages": result.pages_crawled},
            )
    if error:
        raise error
    context.progress(1, 1)
    return {"observation_id": str(observation.pk), "pages_crawled": observation.pages_crawled}


def import_contacts(*, company: Company, observation: WebsiteObservation) -> None:
    """Add missing facts without overwriting manual data or contact restrictions."""
    sources = observation.findings.get("sources", {})
    for field, values in (
        ("email", observation.detected_emails),
        ("phone", observation.detected_phones),
    ):
        normalized = "normalized_email" if field == "email" else "phone_e164"
        for value in values:
            if Contact.objects.filter(
                workspace=company.workspace, company=company, **{normalized: value}
            ).exists():
                continue
            Contact.objects.create(
                workspace=company.workspace,
                company=company,
                full_name="Contacto web",
                **{field: value},
                source_url=sources.get(value, observation.root_url),
                observed_at=observation.crawl_finished_at,
                notes=f"Origen: {observation.provider}; observación {observation.pk}",
            )
    for social in observation.detected_social_urls:
        SocialProfile.objects.get_or_create(
            workspace=company.workspace,
            company=company,
            platform=social["platform"],
            url=social["url"],
            defaults={
                "observed_at": observation.crawl_finished_at,
                "metadata": {
                    "provider": observation.provider,
                    "source_url": social["source_url"],
                    "observation_id": str(observation.pk),
                },
            },
        )
