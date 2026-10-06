"""Worker-only search execution with durable snapshots and one transaction per row."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q

from apps.audit.services import record_event
from apps.jobs.execution import StaleExecution
from apps.jobs.handlers import ExecutionContext
from apps.jobs.models import Job
from common.access import workspace_access
from integrations.common.contracts import PlaceTextSearchRequest
from integrations.common.factory import places_provider

from .acquisition import acquire_google, page_rows
from .ingestion import ingest_row
from .models import SearchPlanItem, SearchResult, SearchRun
from .planner import fingerprint
from .services import eligible_campaign


def execution_job(context: ExecutionContext) -> Job:
    job = Job.objects.get(pk=context.job_id, workspace_id=context.workspace_id)
    if job.status != "RUNNING" or job.attempts != context.attempt:
        raise StaleExecution
    workspace_access(user=job.created_by, workspace_id=context.workspace_id, write=True)
    return job


def run_search(context: ExecutionContext) -> dict:
    """Acquire through the plan's fixed provider, then persist each disposition once."""
    job = execution_job(context)
    item = SearchPlanItem.objects.select_related("plan__campaign").get(
        pk=context.payload["item_id"],
        workspace_id=context.workspace_id,
        job=job,
    )
    eligible_campaign(
        user=job.created_by, workspace_id=context.workspace_id, pk=item.plan.campaign_id
    )
    with transaction.atomic():
        run, _ = SearchRun.objects.get_or_create(
            workspace_id=context.workspace_id,
            job=job,
            defaults={
                "plan_item": item,
                "provider": item.plan.provider,
                "request_fingerprint": fingerprint(
                    {
                        "provider": item.plan.provider,
                        "query": item.query_text,
                        "location": item.location_text,
                        "max_results": item.max_results,
                        "rows": item.import_rows,
                        **(
                            {"country": item.plan.country_code, "mask_version": 1}
                            if item.plan.provider == "GOOGLE_PLACES"
                            else {}
                        ),
                    }
                ),
            },
        )
    if not run.snapshot_ready:
        if item.plan.strategy == "MANUAL_IMPORT":
            rows = item.import_rows
            metadata = {"requests": 0, "simulated": False}
        elif item.plan.provider == "GOOGLE_PLACES":
            provider = places_provider(provider="google")
            rows, metadata = acquire_google(run=run, provider=provider)
        else:
            page = places_provider(provider="mock").text_search(
                PlaceTextSearchRequest(
                    query=item.query_text,
                    location=item.location_text,
                    limit=item.max_results,
                )
            )
            rows = page_rows(page, item.max_results)
            metadata = {"requests": 1, "simulated": True}
        with transaction.atomic():
            execution_job(context)
            run.refresh_from_db()
            run.snapshot = rows
            run.snapshot_ready = True
            run.provider_metadata = metadata
            run.save()
    total = len(run.snapshot)
    for index, row in enumerate(run.snapshot, 1):
        with transaction.atomic():
            job = execution_job(context)
            eligible_campaign(
                user=job.created_by, workspace_id=context.workspace_id, pk=item.plan.campaign_id
            )
            try:
                ingest_row(run=run, user=job.created_by, row=row)
            except ValidationError as error:
                # Only controlled field errors are shown; no arbitrary exception messages.
                fields = (
                    ", ".join(sorted(error.message_dict))
                    if hasattr(error, "message_dict")
                    else "datos"
                )
                SearchResult.objects.get_or_create(
                    workspace_id=context.workspace_id,
                    run=run,
                    line=row["line"],
                    defaults={
                        "error": f"Fila no válida ({fields}); revisa valores o empresa archivada."
                    },
                )
            counts = run.rows.aggregate(
                results_seen=Count("id"),
                companies_created=Count("id", filter=Q(company_created=True)),
                companies_updated=Count("id", filter=Q(company_updated=True)),
                leads_created=Count("id", filter=Q(lead_created=True)),
                duplicates=Count("id", filter=Q(company_created=False, error="")),
                errors=Count("id", filter=~Q(error="")),
            )
            SearchRun.objects.filter(pk=run.pk, workspace_id=context.workspace_id).update(**counts)
            context.progress(index, total)
    with transaction.atomic():
        execution_job(context)
        run.refresh_from_db()
        record_event(
            actor=job.created_by,
            obj=run,
            action="SEARCH_EXECUTED",
            metadata={
                "job_id": str(job.pk),
                "campaign_id": str(item.plan.campaign_id),
                "rows": run.results_seen,
                "errors": run.errors,
            },
        )
    return {
        "search_run_id": str(run.pk),
        "results_seen": run.results_seen,
        "companies_created": run.companies_created,
        "leads_created": run.leads_created,
        "duplicates": run.duplicates,
        "errors": run.errors,
    }
