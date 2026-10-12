from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.jobs.views import JobsView
from common.access import workspace_access
from common.forms import add_service_errors
from common.selectors import scoped_records
from integrations.whatsapp.configuration import configuration_ready

from .email_services import set_live_mode
from .forms import LiveModeForm, WhatsAppConnectionForm, WhatsAppTemplateForm
from .models import InboundWhatsAppMessage, WhatsAppTemplate
from .settings import current_settings
from .whatsapp_selectors import connection_for_user, connection_ready
from .whatsapp_services import configure_connection, create_template, disable_template


class WhatsAppConnectionView(JobsView):
    def page(self, request, form=None, template_form=None, live_form=None, status=200):
        connection = connection_for_user(user=request.user, workspace_id=self.workspace.pk)
        config = current_settings(self.workspace)
        return render(
            request,
            "outreach/whatsapp_connection.html",
            self.context(
                connection=connection,
                configured=configuration_ready(),
                ready=connection_ready(connection),
                global_live=settings.OUTREACH_MODE == "live",
                cloud_selected=settings.WHATSAPP_PROVIDER == "cloud",
                webhook_enabled=settings.WHATSAPP_WEBHOOK_ENABLED,
                config=config,
                callback_path=reverse("whatsapp_webhook", args=[self.workspace.pk]),
                templates=scoped_records(
                    model=WhatsAppTemplate, user=request.user, workspace_id=self.workspace.pk
                ),
                form=form
                or WhatsAppConnectionForm(
                    initial={
                        "enabled": connection.is_enabled if connection else False,
                        "expected_revision": connection.revision if connection else 0,
                    }
                ),
                template_form=template_form or WhatsAppTemplateForm(),
                live_form=live_form
                or LiveModeForm(
                    initial={
                        "enabled": config.live_mode_enabled,
                        "expected_revision": config.revision,
                    }
                ),
            ),
            status=status,
        )

    def get(self, request, **kwargs):
        return self.page(request)

    def post(self, request, **kwargs):
        workspace_access(user=request.user, workspace_id=self.workspace.pk, delete=True)
        action = request.POST.get("action")
        if action == "disable_template":
            from uuid import UUID

            try:
                disable_template(
                    user=request.user,
                    workspace_id=self.workspace.pk,
                    pk=UUID(request.POST.get("template_id", "")),
                )
            except ValueError:
                return self.page(request, status=400)
            return redirect("outreach:whatsapp_connection", workspace_id=self.workspace.pk)
        form_type = {
            "connection": WhatsAppConnectionForm,
            "template": WhatsAppTemplateForm,
            "live": LiveModeForm,
        }.get(action)
        if form_type is None:
            return self.page(request, status=400)
        form = form_type(request.POST)
        if form.is_valid():
            try:
                if action == "template":
                    create_template(
                        user=request.user,
                        workspace_id=self.workspace.pk,
                        data=dict(form.cleaned_data),
                    )
                else:
                    service = set_live_mode if action == "live" else configure_connection
                    service(user=request.user, workspace_id=self.workspace.pk, **form.cleaned_data)
            except ValidationError as error:
                add_service_errors(form, error)
            else:
                return redirect("outreach:whatsapp_connection", workspace_id=self.workspace.pk)
        key = {"connection": "form", "template": "template_form", "live": "live_form"}[action]
        return self.page(request, status=400, **{key: form})


class WhatsAppInboxView(JobsView):
    def get(self, request, **kwargs):
        records = scoped_records(
            model=InboundWhatsAppMessage, user=request.user, workspace_id=self.workspace.pk
        )
        return render(
            request,
            "outreach/whatsapp_inbox.html",
            self.context(page_obj=Paginator(records, 25).get_page(request.GET.get("page"))),
        )
