from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render

from apps.jobs.views import JobsView
from common.access import workspace_access
from common.forms import add_service_errors

from .forms import AssetForm, GenerateForm, ProposalActionForm
from .selectors import assets_for_user, proposal_for_user, proposals_for_user
from .services import (
    archive_asset,
    enqueue_proposal,
    private_document,
    transition_proposal,
    upload_asset,
)


class ProposalListView(JobsView):
    def get(self, request, **kwargs):
        records = proposals_for_user(user=request.user, workspace_id=self.workspace.pk)
        return render(
            request,
            "proposals/list.html",
            self.context(
                jobs_active=False, page_obj=Paginator(records, 25).get_page(request.GET.get("page"))
            ),
        )


class GenerateView(JobsView):
    def get(self, request, **kwargs):
        workspace_access(user=request.user, workspace_id=self.workspace.pk, write=True)
        form = GenerateForm(user=request.user, workspace_id=self.workspace.pk)
        return render(request, "proposals/generate.html", self.context(form=form))

    def post(self, request, **kwargs):
        workspace_access(user=request.user, workspace_id=self.workspace.pk, write=True)
        form = GenerateForm(request.POST, user=request.user, workspace_id=self.workspace.pk)
        if form.is_valid():
            data = form.cleaned_data
            try:
                job = enqueue_proposal(
                    user=request.user,
                    workspace_id=self.workspace.pk,
                    lead_id=data["lead"].pk,
                    provider=data["provider"],
                    asset_ids=[str(asset.pk) for asset in data["assets"]],
                    idempotency_key=str(data["idempotency_key"]),
                )
            except ValidationError as error:
                add_service_errors(form, error)
            else:
                return redirect("jobs:detail", workspace_id=self.workspace.pk, pk=job.pk)
        return render(request, "proposals/generate.html", self.context(form=form), status=400)


class ProposalDetailView(JobsView):
    def get(self, request, pk, **kwargs):
        proposal = proposal_for_user(user=request.user, workspace_id=self.workspace.pk, pk=pk)
        return render(
            request, "proposals/detail.html", self.context(proposal=proposal, jobs_active=False)
        )

    def post(self, request, pk, **kwargs):
        form = ProposalActionForm(request.POST)
        proposal = proposal_for_user(user=request.user, workspace_id=self.workspace.pk, pk=pk)
        if form.is_valid():
            try:
                transition_proposal(
                    user=request.user,
                    workspace_id=self.workspace.pk,
                    pk=pk,
                    target=form.cleaned_data["action"],
                    expected_revision=form.cleaned_data["expected_revision"],
                )
            except ValidationError as error:
                add_service_errors(form, error)
            else:
                return redirect("proposals:detail", workspace_id=self.workspace.pk, pk=pk)
        return render(
            request, "proposals/detail.html", self.context(proposal=proposal, form=form), status=400
        )


class PrintView(JobsView):
    def get(self, request, pk, **kwargs):
        proposal = proposal_for_user(user=request.user, workspace_id=self.workspace.pk, pk=pk)
        return render(request, "proposals/print.html", {"proposal": proposal})


class AssetsView(JobsView):
    def page(self, request, form, status=200):
        records = assets_for_user(user=request.user, workspace_id=self.workspace.pk)
        return render(
            request,
            "proposals/assets.html",
            self.context(
                form=form,
                jobs_active=False,
                page_obj=Paginator(records, 25).get_page(request.GET.get("page")),
            ),
            status=status,
        )

    def get(self, request, **kwargs):
        return self.page(request, AssetForm())

    def post(self, request, **kwargs):
        workspace_access(user=request.user, workspace_id=self.workspace.pk, delete=True)
        form = AssetForm(request.POST, request.FILES)
        if form.is_valid():
            data = dict(form.cleaned_data)
            upload = data.pop("file")
            try:
                upload_asset(
                    user=request.user, workspace_id=self.workspace.pk, upload=upload, **data
                )
            except ValidationError as error:
                add_service_errors(form, error)
            else:
                return redirect("proposals:assets", workspace_id=self.workspace.pk)
        return self.page(request, form, status=400)


class ArchiveAssetView(JobsView):
    def post(self, request, pk, **kwargs):
        archive_asset(user=request.user, workspace_id=self.workspace.pk, pk=pk)
        return redirect("proposals:assets", workspace_id=self.workspace.pk)


class DownloadView(JobsView):
    def get(self, request, pk, asset=False, **kwargs):
        try:
            file = private_document(
                user=request.user, workspace_id=self.workspace.pk, pk=pk, asset=asset
            )
        except ValidationError as error:
            raise Http404 from error
        response = FileResponse(
            file,
            as_attachment=True,
            filename=f"{'asset' if asset else 'proposal'}-{pk}.pdf",
            content_type="application/pdf",
        )
        response["Cache-Control"] = "private, no-store"
        response["Content-Security-Policy"] = "sandbox; default-src 'none'"
        return response
