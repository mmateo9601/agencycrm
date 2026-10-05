from django.contrib import admin

from common.admin import DomainReadOnlyAdmin

from .models import DuplicateCandidate, SearchPlan, SearchPlanItem, SearchResult, SearchRun

for model in (SearchPlan, SearchPlanItem, SearchRun, SearchResult, DuplicateCandidate):
    admin.site.register(model, DomainReadOnlyAdmin)
