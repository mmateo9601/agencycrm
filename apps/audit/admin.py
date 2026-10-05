from django.contrib import admin

from common.admin import DomainReadOnlyAdmin

from .models import AuditEvent

admin.site.register(AuditEvent, DomainReadOnlyAdmin)
