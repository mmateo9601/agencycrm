from django.contrib import admin

from common.admin import DomainReadOnlyAdmin

from .models import ContactEligibilityDecision, SuppressionEntry

for model in (ContactEligibilityDecision, SuppressionEntry):
    admin.site.register(model, DomainReadOnlyAdmin)
