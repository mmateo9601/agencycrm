from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import redirect, render

from apps.jobs.views import JobsView
from common.access import workspace_access
from common.forms import add_service_errors

from .forms import PlanForm, RerunForm
from .selectors import (
    duplicates_for_user,
    plan_for_user,
    plan_items,
    plan_search_runs,
    plans_for_user,
    results_for_user,
    run_for_user,
)
from .services import create_plan, enqueue_item


class ProspectingView(JobsView):
    def context(self, **kwargs):
        return super().context(**({"jobs_active": False, "prospecting_active": True} | kwargs))


class PlanListView(ProspectingView):
    def get(self, request, **kwargs):
        plans = plans_for_user(user=request.user, workspace_id=self.workspace.pk)
        return render(
            request,
            "prospecting/list.html",
            self.context(page_obj=Paginator(plans, 25).get_page(request.GET.get("page"))),
        )


class PlanCreateView(ProspectingView):
    def get(self, request, **kwargs):
        workspace_access(user=request.user, workspace_id=self.workspace.pk, write=True)
        form = PlanForm(user=request.user, workspace_id=self.workspace.pk)
        return render(request, "prospecting/create.html", self.context(form=form))

    def post(self, request, **kwargs):
        workspace_access(user=request.user, workspace_id=self.workspace.pk, write=True)
        form = PlanForm(
            request.POST, request.FILES, user=request.user, workspace_id=self.workspace.pk
        )
        if form.is_valid():
            data = form.cleaned_data
            try:
                plan = create_plan(
                    user=request.user,
                    workspace_id=self.workspace.pk,
                    campaign_id=data["campaign"].pk,
                    request_key=data["request_key"],
                    locations=data["locations"],
                    csv_content=data["csv_file"],
                )
            except ValidationError as error:
                add_service_errors(form, error)
            else:
                return redirect("prospecting:detail", workspace_id=self.workspace.pk, pk=plan.pk)
        return render(request, "prospecting/create.html", self.context(form=form), status=400)


class PlanDetailView(ProspectingView):
    def get(self, request, pk, **kwargs):
        plan = plan_for_user(user=request.user, workspace_id=self.workspace.pk, pk=pk)
        items = plan_items(plan)
        return render(
            request,
            "prospecting/detail.html",
            self.context(
                plan=plan,
                items=items,
                rerun_form=RerunForm(),
                page_obj=Paginator(plan_search_runs(plan), 25).get_page(request.GET.get("page")),
            ),
        )


class RunDetailView(ProspectingView):
    def get(self, request, pk, **kwargs):
        run = run_for_user(user=request.user, workspace_id=self.workspace.pk, pk=pk)
        rows = results_for_user(user=request.user, workspace_id=self.workspace.pk, run_id=pk)
        return render(
            request,
            "prospecting/run.html",
            self.context(run=run, page_obj=Paginator(rows, 25).get_page(request.GET.get("page"))),
        )


class RerunView(ProspectingView):
    def post(self, request, pk, **kwargs):
        form = RerunForm(request.POST)
        if not form.is_valid():
            return render(request, "prospecting/create.html", self.context(form=form), status=400)
        try:
            job = enqueue_item(
                user=request.user,
                workspace_id=self.workspace.pk,
                pk=pk,
                request_key=form.cleaned_data["request_key"],
            )
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
            return redirect("prospecting:list", workspace_id=self.workspace.pk)
        return redirect("jobs:detail", workspace_id=self.workspace.pk, pk=job.pk)


class DuplicateListView(ProspectingView):
    def get(self, request, **kwargs):
        records = duplicates_for_user(user=request.user, workspace_id=self.workspace.pk)
        return render(
            request,
            "prospecting/duplicates.html",
            self.context(page_obj=Paginator(records, 25).get_page(request.GET.get("page"))),
        )
