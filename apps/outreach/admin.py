from django.contrib import admin

from common.admin import DomainReadOnlyAdmin

from .models import MessageAttachment, MessageEvent, OutreachMessage, OutreachSettings, SalesTask

for model in (OutreachSettings, OutreachMessage, MessageAttachment, MessageEvent, SalesTask):
    admin.site.register(model, DomainReadOnlyAdmin)
