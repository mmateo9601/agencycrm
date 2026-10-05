from django.contrib import admin

from common.admin import DomainReadOnlyAdmin

from .models import Lead, LeadStatusHistory, Opportunity

for model in (Lead, LeadStatusHistory, Opportunity):
    admin.site.register(model, DomainReadOnlyAdmin)
