from django.contrib import admin

from common.admin import DomainReadOnlyAdmin

from .models import (
    EmailConnection,
    InboundWhatsAppMessage,
    MessageAttachment,
    MessageEvent,
    OutreachMessage,
    OutreachSettings,
    SalesTask,
    WhatsAppConnection,
    WhatsAppTemplate,
)

for model in (
    WhatsAppConnection,
    WhatsAppTemplate,
    InboundWhatsAppMessage,
    EmailConnection,
    OutreachSettings,
    OutreachMessage,
    MessageAttachment,
    MessageEvent,
    SalesTask,
):
    admin.site.register(model, DomainReadOnlyAdmin)
