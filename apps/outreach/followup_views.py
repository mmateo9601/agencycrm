from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import redirect, render

from apps.jobs.views import JobsView
from common.access import workspace_access
from common.forms import add_service_errors
from common.selectors import scoped_record, scoped_records

from .followup_forms import FollowUpRuleForm, ScheduleFollowUpsForm
from .followups import save_rule, schedule_source
from .models import FollowUpRule


class FollowUpRulesView(JobsView):
    def page(self, request, pk=None, form=None, schedule_form=None, status=200):
        rule = (
            scoped_record(
                model=FollowUpRule, user=request.user, workspace_id=self.workspace.pk, pk=pk
            )
            if pk
            else None
        )
        return render(
            request,
            "outreach/followup_rules.html",
            self.context(
                form=form
                or FollowUpRuleForm(
                    user=request.user,
                    workspace_id=self.workspace.pk,
                    instance=rule,
                    initial={"expected_revision": rule.revision if rule else 0},
                ),
                schedule_form=schedule_form
                or ScheduleFollowUpsForm(user=request.user, workspace_id=self.workspace.pk),
                page_obj=Paginator(
                    scoped_records(
                        model=FollowUpRule, user=request.user, workspace_id=self.workspace.pk
                    ),
                    25,
                ).get_page(request.GET.get("page")),
                editing=rule,
            ),
            status=status,
        )

    def get(self, request, pk=None, **kwargs):
        return self.page(request, pk)

    def post(self, request, pk=None, **kwargs):
        if request.POST.get("action") == "schedule":
            workspace_access(user=request.user, workspace_id=self.workspace.pk, write=True)
            form = ScheduleFollowUpsForm(
                request.POST, user=request.user, workspace_id=self.workspace.pk
            )
            if form.is_valid():
                try:
                    schedule_source(
                        user=request.user,
                        workspace_id=self.workspace.pk,
                        source_id=form.cleaned_data["source"].pk,
                    )
                except ValidationError as error:
                    add_service_errors(form, error)
                else:
                    return redirect("outreach:tasks", workspace_id=self.workspace.pk)
            return self.page(request, pk, schedule_form=form, status=400)
        workspace_access(user=request.user, workspace_id=self.workspace.pk, delete=True)
        rule = (
            scoped_record(
                model=FollowUpRule, user=request.user, workspace_id=self.workspace.pk, pk=pk
            )
            if pk
            else None
        )
        form = FollowUpRuleForm(
            request.POST, user=request.user, workspace_id=self.workspace.pk, instance=rule
        )
        if form.is_valid():
            data = dict(form.cleaned_data)
            revision = data.pop("expected_revision")
            try:
                save_rule(
                    user=request.user,
                    workspace_id=self.workspace.pk,
                    pk=pk,
                    data=data,
                    expected_revision=revision,
                )
            except ValidationError as error:
                add_service_errors(form, error)
            else:
                return redirect("outreach:followup_rules", workspace_id=self.workspace.pk)
        return self.page(request, pk, form=form, status=400)
