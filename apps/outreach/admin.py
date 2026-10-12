from django.contrib import admin

from common.admin import DomainReadOnlyAdmin

from .models import (
    EmailConnection,
    MessageAttachment,
    MessageEvent,
    OutreachMessage,
    OutreachSettings,
    SalesTask,
)

for model in (
    EmailConnection,
    OutreachSettings,
    OutreachMessage,
    MessageAttachment,
    MessageEvent,
    SalesTask,
):
    admin.site.register(model, DomainReadOnlyAdmin)
