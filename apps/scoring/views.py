from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from apps.jobs.views import JobsView
from common.access import workspace_access
from common.forms import add_service_errors
from common.selectors import scoped_record

from .forms import RuleForm, ScoreForm
from .models import ScoringRule
from .selectors import scoring_lead, scoring_rules
from .services import enqueue_score, initialize_rules, save_rule


class ScoreView(JobsView):
    def page(self, request, pk, form, status=200):
        lead = scoring_lead(user=request.user, workspace_id=self.workspace.pk, pk=pk)
        runs = lead.scoring_runs.filter(workspace=self.workspace)
        try:
            selected_id = UUID(request.GET["run"]) if "run" in request.GET else None
        except (ValueError, TypeError) as error:
            raise Http404 from error
        selected = (
            get_object_or_404(runs, pk=selected_id)
            if "run" in request.GET
            else runs.filter(pk=lead.current_score_run_id).first()
        )
        return render(
            request,
            "scoring/lead.html",
            self.context(
                lead=lead,
                selected=selected,
                form=form,
                jobs_active=False,
                page_obj=Paginator(runs, 10).get_page(request.GET.get("page")),
            ),
            status=status,
        )

    def get(self, request, pk, **kwargs):
        return self.page(request, pk, ScoreForm())

    def post(self, request, pk, **kwargs):
        workspace_access(user=request.user, workspace_id=self.workspace.pk, write=True)
        form = ScoreForm(request.POST)
        if form.is_valid():
            try:
                job = enqueue_score(
                    user=request.user,
                    workspace_id=self.workspace.pk,
                    lead_id=pk,
                    idempotency_key=str(form.cleaned_data["idempotency_key"]),
                )
            except ValidationError as error:
                add_service_errors(form, error)
            else:
                return redirect("jobs:detail", workspace_id=self.workspace.pk, pk=job.pk)
        return self.page(request, pk, form, status=400)


class RulesView(JobsView):
    def get(self, request, **kwargs):
        rules = scoring_rules(user=request.user, workspace_id=self.workspace.pk)
        return render(
            request,
            "scoring/rules.html",
            self.context(
                page_obj=Paginator(rules, 25).get_page(request.GET.get("page")), jobs_active=False
            ),
        )

    def post(self, request, **kwargs):
        workspace_access(user=request.user, workspace_id=self.workspace.pk, delete=True)
        initialize_rules(user=request.user, workspace_id=self.workspace.pk)
        return redirect("scoring:rules", workspace_id=self.workspace.pk)


class RuleEditView(JobsView):
    def rule(self, request, pk):
        workspace_access(user=request.user, workspace_id=self.workspace.pk, delete=True)
        return scoped_record(
            model=ScoringRule, user=request.user, workspace_id=self.workspace.pk, pk=pk
        )

    def get(self, request, pk, **kwargs):
        rule = self.rule(request, pk)
        form = RuleForm(instance=rule, initial={"expected_version": rule.version})
        return render(request, "scoring/rule_form.html", self.context(form=form, rule=rule))

    def post(self, request, pk, **kwargs):
        rule = self.rule(request, pk)
        form = RuleForm(request.POST, instance=rule)
        if form.is_valid():
            data = dict(form.cleaned_data)
            expected = data.pop("expected_version")
            try:
                save_rule(
                    user=request.user,
                    workspace_id=self.workspace.pk,
                    pk=pk,
                    data=data,
                    expected_version=expected,
                )
            except ValidationError as error:
                add_service_errors(form, error)
            else:
                return redirect("scoring:rules", workspace_id=self.workspace.pk)
        return render(
            request, "scoring/rule_form.html", self.context(form=form, rule=rule), status=400
        )
