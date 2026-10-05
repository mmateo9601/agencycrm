from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache

from common.access import workspace_access, workspace_permissions
from common.forms import add_service_errors
from common.resources import RESOURCES

from .forms import DiagnosticJobForm, JobFilterForm
from .locking import WorkerAlreadyRunning
from .models import Job
from .selectors import is_stale, job_for_user, jobs_for_user, progress_snapshot
from .services import JobService


class JobsView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.workspace = workspace_access(user=request.user, workspace_id=kwargs["workspace_id"])
        return super().dispatch(request, *args, **kwargs)

    def context(self, **kwargs):
        return {
            "workspace": self.workspace,
            "resources": RESOURCES.values(),
            "jobs_active": True,
            **workspace_permissions(user=self.request.user, workspace=self.workspace),
            **kwargs,
        }

    def job(self, pk):
        return job_for_user(user=self.request.user, workspace_id=self.workspace.pk, pk=pk)


class JobListView(JobsView):
    def get(self, request, **kwargs):
        form = JobFilterForm(request.GET)
        jobs = jobs_for_user(
            user=request.user,
            workspace_id=self.workspace.pk,
            filters=form.cleaned_data if form.is_valid() else {},
        )
        if not form.is_valid():
            jobs = jobs.none()
        page = Paginator(jobs, 25).get_page(request.GET.get("page"))
        return render(request, "jobs/list.html", self.context(page_obj=page, filter_form=form))


class JobCreateView(JobsView):
    def get(self, request, **kwargs):
        workspace_access(user=request.user, workspace_id=self.workspace.pk, write=True)
        return render(request, "jobs/create.html", self.context(form=DiagnosticJobForm()))

    def post(self, request, **kwargs):
        workspace_access(user=request.user, workspace_id=self.workspace.pk, write=True)
        form = DiagnosticJobForm(request.POST)
        if form.is_valid():
            data = dict(form.cleaned_data)
            payload = {"scenario": data.pop("scenario")}
            try:
                job = JobService.enqueue(
                    user=request.user,
                    workspace_id=self.workspace.pk,
                    kind=Job.Kind.CHECK_MOCK_PROVIDERS,
                    payload=payload,
                    **data,
                )
            except ValidationError as error:
                add_service_errors(form, error)
            else:
                return redirect("jobs:detail", workspace_id=self.workspace.pk, pk=job.pk)
        return render(request, "jobs/create.html", self.context(form=form), status=400)


class JobDetailView(JobsView):
    def get(self, request, pk, **kwargs):
        job = self.job(pk)
        attempts = Paginator(job.attempt_history.all(), 10).get_page(request.GET.get("page"))
        return render(
            request,
            "jobs/detail.html",
            self.context(job=job, attempts_page=attempts, stale=is_stale(job)),
        )


@method_decorator(never_cache, name="dispatch")
class JobProgressView(JobsView):
    def get(self, request, pk, **kwargs):
        return JsonResponse(progress_snapshot(self.job(pk)))


class JobActionView(JobsView):
    def post(self, request, pk, action, **kwargs):
        self.job(pk)
        try:
            if action == "cancel":
                JobService.cancel(user=request.user, workspace_id=self.workspace.pk, pk=pk)
                messages.success(request, "Trabajo cancelado.")
            elif action == "recover":
                result = JobService.recover(
                    user=request.user, workspace_id=self.workspace.pk, pk=pk
                )
                messages.success(
                    request, f"Reencolados: {result['requeued']}. Agotados: {result['failed']}."
                )
            else:
                raise Http404
        except (ValidationError, WorkerAlreadyRunning) as error:
            messages.error(
                request,
                " ".join(error.messages) if isinstance(error, ValidationError) else str(error),
            )
        return redirect("jobs:detail", workspace_id=self.workspace.pk, pk=pk)
