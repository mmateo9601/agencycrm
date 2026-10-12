import uuid

from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import redirect, render

from apps.jobs.models import Job
from apps.jobs.services import JobService
from apps.jobs.views import JobsView
from common.access import require_capability
from common.exports import export_csv
from common.filters import RecordFilterForm
from common.forms import add_service_errors
from common.resources import RESOURCES

from .forms import AuditFilterForm, ExportForm, RetentionForm
from .selectors import events_for_user


class AuditView(JobsView):
    def get(self, request, **kwargs):
        form = AuditFilterForm(request.GET)
        records = events_for_user(
            user=request.user,
            workspace_id=self.workspace.pk,
            filters=form.cleaned_data if form.is_valid() else {},
        )
        if not form.is_valid():
            records = records.none()
        return render(
            request,
            "audit/events.html",
            self.context(
                filter_form=form,
                page_obj=Paginator(records, 25).get_page(request.GET.get("page")),
                jobs_active=False,
            ),
        )


class ExportView(JobsView):
    def get(self, request, **kwargs):
        require_capability(user=request.user, workspace_id=self.workspace.pk, capability="export")
        return render(request, "audit/export.html", self.context(form=ExportForm()))

    def post(self, request, **kwargs):
        require_capability(user=request.user, workspace_id=self.workspace.pk, capability="export")
        form = ExportForm(request.POST)
        if form.is_valid():
            resource = form.cleaned_data["resource"]
            filters = RecordFilterForm(
                request.POST, model=RESOURCES[resource].model, workspace=self.workspace
            )
            if not filters.is_valid():
                form.add_error(None, "Filtros no válidos para este espacio.")
            else:
                try:
                    data = export_csv(
                        user=request.user,
                        workspace_id=self.workspace.pk,
                        resource=resource,
                        filters=filters.cleaned_data,
                    )
                except ValidationError as error:
                    add_service_errors(form, error)
                else:
                    response = HttpResponse(data, content_type="text/csv; charset=utf-8")
                    response["Content-Disposition"] = (
                        f'attachment; filename="{resource}-{self.workspace.pk}.csv"'
                    )
                    response["Content-Security-Policy"] = "sandbox; default-src 'none'"
                    return response
        return render(request, "audit/export.html", self.context(form=form), status=400)


class RetentionView(JobsView):
    def get(self, request, **kwargs):
        require_capability(
            user=request.user, workspace_id=self.workspace.pk, capability="retention"
        )
        return render(
            request,
            "audit/retention.html",
            self.context(form=RetentionForm(initial={"request_key": uuid.uuid4()})),
        )

    def post(self, request, **kwargs):
        require_capability(
            user=request.user, workspace_id=self.workspace.pk, capability="retention"
        )
        form = RetentionForm(request.POST)
        if form.is_valid():
            try:
                job = JobService.enqueue(
                    user=request.user,
                    workspace_id=self.workspace.pk,
                    kind=Job.Kind.RETENTION,
                    payload={"inbound_days": form.cleaned_data["inbound_days"]},
                    idempotency_key=f"retention:{form.cleaned_data['request_key']}",
                    max_attempts=1,
                )
            except ValidationError as error:
                add_service_errors(form, error)
            else:
                return redirect("jobs:detail", workspace_id=self.workspace.pk, pk=job.pk)
        return render(request, "audit/retention.html", self.context(form=form), status=400)
