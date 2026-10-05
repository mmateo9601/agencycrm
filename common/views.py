from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect, render
from django.views import View

from apps.campaigns.services import change_campaign_status
from apps.crm.forms import LeadTransitionForm
from apps.crm.services import LeadStateService
from common.access import workspace_access, workspace_permissions
from common.filters import RecordFilterForm
from common.forms import add_service_errors
from common.presentation import detail_context
from common.resources import RESOURCES
from common.selectors import scoped_record


class WorkspaceView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.workspace = workspace_access(user=request.user, workspace_id=kwargs["workspace_id"])
        self.resource = RESOURCES.get(kwargs["resource"])
        if self.resource is None:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def context(self, **kwargs):
        return {
            "workspace": self.workspace,
            "resource": self.resource,
            "resources": RESOURCES.values(),
            **workspace_permissions(user=self.request.user, workspace=self.workspace),
            **kwargs,
        }

    def record(self, pk):
        return scoped_record(
            model=self.resource.model, user=self.request.user, workspace_id=self.workspace.pk, pk=pk
        )

    def form(self, data=None, pk=None):
        return self.resource.form(
            data,
            user=self.request.user,
            workspace_id=self.workspace.pk,
            instance=self.record(pk) if pk else None,
        )


class RecordListView(WorkspaceView):
    def get(self, request, **kwargs):
        form = RecordFilterForm(request.GET, model=self.resource.model, workspace=self.workspace)
        records = self.resource.select(
            model=self.resource.model,
            user=request.user,
            workspace_id=self.workspace.pk,
            filters=form.cleaned_data if form.is_valid() else {},
        )
        if not form.is_valid():
            records = records.none()
        page = Paginator(records, 25).get_page(request.GET.get("page"))
        return render(request, "workspace/list.html", self.context(filter_form=form, page_obj=page))


class RecordDetailView(WorkspaceView):
    def get(self, request, pk, **kwargs):
        obj = self.record(pk)
        return render(
            request,
            "workspace/detail.html",
            self.context(object=obj, **detail_context(obj=obj, params=request.GET)),
        )


class RecordEditView(WorkspaceView):
    def get(self, request, pk=None, **kwargs):
        workspace_access(user=request.user, workspace_id=self.workspace.pk, write=True)
        return render(
            request, "workspace/form.html", self.context(form=self.form(pk=pk), editing=bool(pk))
        )

    def post(self, request, pk=None, **kwargs):
        workspace_access(user=request.user, workspace_id=self.workspace.pk, write=True)
        form = self.form(request.POST, pk)
        if form.is_valid():
            try:
                obj = self.resource.save(
                    user=request.user, workspace_id=self.workspace.pk, data=form.cleaned_data, pk=pk
                )
            except ValidationError as exc:
                add_service_errors(form, exc)
            else:
                messages.success(request, "Registro guardado.")
                return redirect(
                    "workspace:detail",
                    workspace_id=self.workspace.pk,
                    resource=self.resource.key,
                    pk=obj.pk,
                )
        return render(
            request, "workspace/form.html", self.context(form=form, editing=bool(pk)), status=400
        )


class RecordDeleteView(WorkspaceView):
    def get(self, request, pk, **kwargs):
        workspace_access(user=request.user, workspace_id=self.workspace.pk, delete=True)
        return render(request, "workspace/delete.html", self.context(object=self.record(pk)))

    def post(self, request, pk, **kwargs):
        workspace_access(user=request.user, workspace_id=self.workspace.pk, delete=True)
        obj = self.record(pk)
        try:
            self.resource.delete(user=request.user, workspace_id=self.workspace.pk, pk=pk)
        except ValidationError as exc:
            return render(
                request,
                "workspace/delete.html",
                self.context(object=obj, error=" ".join(exc.messages)),
                status=400,
            )
        messages.success(request, "Operación completada.")
        return redirect(
            "workspace:list", workspace_id=self.workspace.pk, resource=self.resource.key
        )


class StateView(WorkspaceView):
    def post(self, request, pk, **kwargs):
        workspace_access(user=request.user, workspace_id=self.workspace.pk, write=True)
        obj = self.record(pk)
        context = detail_context(obj=obj, params=request.GET)
        if self.resource.key == "leads":
            form = LeadTransitionForm(request.POST, lead=obj)
            context["transition_form"] = form
            if form.is_valid():
                try:
                    LeadStateService.transition(
                        user=request.user,
                        workspace_id=self.workspace.pk,
                        pk=pk,
                        **form.cleaned_data,
                    )
                except ValidationError as exc:
                    add_service_errors(form, exc)
                else:
                    return redirect(
                        "workspace:detail",
                        workspace_id=self.workspace.pk,
                        resource=self.resource.key,
                        pk=pk,
                    )
        elif self.resource.key == "campaigns":
            try:
                change_campaign_status(
                    user=request.user,
                    workspace_id=self.workspace.pk,
                    pk=pk,
                    target=request.POST.get("target", ""),
                )
            except ValidationError as exc:
                context["state_error"] = " ".join(exc.messages)
            else:
                return redirect(
                    "workspace:detail",
                    workspace_id=self.workspace.pk,
                    resource=self.resource.key,
                    pk=pk,
                )
        else:
            raise Http404
        return render(
            request, "workspace/detail.html", self.context(object=obj, **context), status=400
        )
