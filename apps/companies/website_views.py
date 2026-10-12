import uuid

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import redirect, render

from apps.jobs.views import JobsView
from common.access import workspace_access
from common.forms import add_service_errors

from .enrichment import enqueue_enrichment
from .website_selectors import company_observations


class EnrichmentForm(forms.Form):
    idempotency_key = forms.UUIDField(initial=uuid.uuid4, widget=forms.HiddenInput)


class EnrichmentView(JobsView):
    def page(self, request, pk, form, status=200):
        company, observations = company_observations(
            user=request.user, workspace_id=self.workspace.pk, company_id=pk
        )
        return render(
            request,
            "companies/enrichment.html",
            self.context(
                company=company,
                form=form,
                jobs_active=False,
                provider=settings.WEBSITE_PROVIDER,
                page_obj=Paginator(observations, 10).get_page(request.GET.get("page")),
            ),
            status=status,
        )

    def get(self, request, pk, **kwargs):
        return self.page(request, pk, EnrichmentForm())

    def post(self, request, pk, **kwargs):
        workspace_access(user=request.user, workspace_id=self.workspace.pk, write=True)
        form = EnrichmentForm(request.POST)
        if form.is_valid():
            try:
                job = enqueue_enrichment(
                    user=request.user,
                    workspace_id=self.workspace.pk,
                    company_id=pk,
                    idempotency_key=str(form.cleaned_data["idempotency_key"]),
                )
            except ValidationError as error:
                add_service_errors(form, error)
            else:
                return redirect("jobs:detail", workspace_id=self.workspace.pk, pk=job.pk)
        return self.page(request, pk, form, status=400)
