from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect, render

from apps.compliance.forms import SuppressionForm
from apps.compliance.models import SuppressionEntry
from apps.compliance.policies import describe_reasons, eligibility
from apps.compliance.services import add_suppression, evaluate, expire_suppression
from apps.jobs.views import JobsView
from common.access import workspace_access
from common.forms import add_service_errors
from common.selectors import scoped_records

from .forms import ApproveForm, PrepareForm, SettingsForm
from .models import SalesTask
from .selectors import message_for_user, messages_for_user
from .services import (
    approve_message,
    complete_task,
    configure,
    enqueue_message,
    pause,
    prepare_message,
)
from .settings import current_settings


class OutboxView(JobsView):
    def get(self, request, **kwargs):
        records = messages_for_user(user=request.user, workspace_id=self.workspace.pk)
        return render(
            request,
            "outreach/outbox.html",
            self.context(
                jobs_active=False, page_obj=Paginator(records, 25).get_page(request.GET.get("page"))
            ),
        )


class PrepareView(JobsView):
    def get(self, request, **kwargs):
        workspace_access(user=request.user, workspace_id=self.workspace.pk, write=True)
        return render(
            request,
            "outreach/prepare.html",
            self.context(form=PrepareForm(user=request.user, workspace_id=self.workspace.pk)),
        )

    def post(self, request, **kwargs):
        workspace_access(user=request.user, workspace_id=self.workspace.pk, write=True)
        form = PrepareForm(request.POST, user=request.user, workspace_id=self.workspace.pk)
        if form.is_valid():
            data = dict(form.cleaned_data)
            proposal, contact = data.pop("proposal"), data.pop("contact")
            data["idempotency_key"] = str(data["idempotency_key"])
            try:
                message = prepare_message(
                    user=request.user,
                    workspace_id=self.workspace.pk,
                    proposal_id=proposal.pk,
                    contact_id=contact.pk if contact else None,
                    **data,
                )
            except ValidationError as error:
                add_service_errors(form, error)
            else:
                return redirect("outreach:message", workspace_id=self.workspace.pk, pk=message.pk)
        return render(request, "outreach/prepare.html", self.context(form=form), status=400)


class MessageView(JobsView):
    def page(self, request, pk, form=None, error="", status=200):
        message = message_for_user(user=request.user, workspace_id=self.workspace.pk, pk=pk)
        verdict, reasons = eligibility(message)
        return render(
            request,
            "outreach/message.html",
            self.context(
                message=message,
                verdict=verdict,
                reasons=describe_reasons(reasons),
                form=form or ApproveForm(initial={"expected_revision": message.revision}),
                error=error,
                decisions=Paginator(message.eligibility_decisions.all(), 10).get_page(
                    request.GET.get("page")
                ),
                events=message.events.all()[:25],
                jobs_active=False,
            ),
            status=status,
        )

    def get(self, request, pk, **kwargs):
        return self.page(request, pk)

    def post(self, request, pk, **kwargs):
        workspace_access(user=request.user, workspace_id=self.workspace.pk, write=True)
        action = request.POST.get("action")
        try:
            if action == "approve":
                form = ApproveForm(request.POST)
                if not form.is_valid():
                    return self.page(request, pk, form=form, status=400)
                approve_message(
                    user=request.user, workspace_id=self.workspace.pk, pk=pk, **form.cleaned_data
                )
            elif action == "enqueue":
                enqueue_message(user=request.user, workspace_id=self.workspace.pk, pk=pk)
            elif action == "evaluate":
                evaluate(user=request.user, workspace_id=self.workspace.pk, message_id=pk)
            else:
                raise Http404
        except ValidationError as error:
            return self.page(request, pk, error="; ".join(error.messages), status=400)
        return redirect("outreach:message", workspace_id=self.workspace.pk, pk=pk)


class SuppressionView(JobsView):
    def page(self, request, form=None, status=200):
        records = scoped_records(
            model=SuppressionEntry, user=request.user, workspace_id=self.workspace.pk
        )
        return render(
            request,
            "outreach/suppression.html",
            self.context(
                form=form or SuppressionForm(user=request.user, workspace_id=self.workspace.pk),
                page_obj=Paginator(records, 25).get_page(request.GET.get("page")),
                jobs_active=False,
            ),
            status=status,
        )

    def get(self, request, **kwargs):
        return self.page(request)

    def post(self, request, **kwargs):
        workspace_access(user=request.user, workspace_id=self.workspace.pk, delete=True)
        form = SuppressionForm(request.POST, user=request.user, workspace_id=self.workspace.pk)
        if form.is_valid():
            data = dict(form.cleaned_data)
            company = data.pop("company")
            try:
                add_suppression(
                    user=request.user,
                    workspace_id=self.workspace.pk,
                    company_id=company.pk if company else None,
                    **data,
                )
            except ValidationError as error:
                add_service_errors(form, error)
            else:
                return redirect("outreach:suppression", workspace_id=self.workspace.pk)
        return self.page(request, form=form, status=400)


class ExpireSuppressionView(JobsView):
    def post(self, request, pk, **kwargs):
        expire_suppression(user=request.user, workspace_id=self.workspace.pk, pk=pk)
        return redirect("outreach:suppression", workspace_id=self.workspace.pk)


class SettingsView(JobsView):
    def get(self, request, **kwargs):
        config = current_settings(self.workspace)
        return render(
            request,
            "outreach/settings.html",
            self.context(
                config=config,
                form=SettingsForm(instance=config, initial={"expected_revision": config.revision}),
            ),
        )

    def post(self, request, **kwargs):
        workspace_access(user=request.user, workspace_id=self.workspace.pk, delete=True)
        if request.POST.get("action") in {"pause", "resume"}:
            pause(
                user=request.user,
                workspace_id=self.workspace.pk,
                paused=request.POST["action"] == "pause",
            )
            return redirect("outreach:settings", workspace_id=self.workspace.pk)
        config = current_settings(self.workspace)
        form = SettingsForm(request.POST, instance=config)
        if form.is_valid():
            data = dict(form.cleaned_data)
            revision = data.pop("expected_revision")
            try:
                configure(
                    user=request.user,
                    workspace_id=self.workspace.pk,
                    data=data,
                    expected_revision=revision,
                )
            except ValidationError as error:
                add_service_errors(form, error)
            else:
                return redirect("outreach:settings", workspace_id=self.workspace.pk)
        return render(
            request, "outreach/settings.html", self.context(config=config, form=form), status=400
        )


class TasksView(JobsView):
    def get(self, request, **kwargs):
        records = scoped_records(
            model=SalesTask, user=request.user, workspace_id=self.workspace.pk
        ).order_by("due_at", "id")
        return render(
            request,
            "outreach/tasks.html",
            self.context(
                page_obj=Paginator(records, 25).get_page(request.GET.get("page")), jobs_active=False
            ),
        )

    def post(self, request, **kwargs):
        try:
            from uuid import UUID

            pk = UUID(request.POST.get("task_id", ""))
            complete_task(
                user=request.user,
                workspace_id=self.workspace.pk,
                pk=pk,
                outcome=request.POST.get("outcome", ""),
            )
        except ValueError, ValidationError:
            return render(
                request,
                "outreach/error.html",
                self.context(error="Tarea o resultado no válido."),
                status=400,
            )
        return redirect("outreach:tasks", workspace_id=self.workspace.pk)
