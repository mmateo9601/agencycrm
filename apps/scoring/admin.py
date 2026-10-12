from django.contrib import admin

from common.admin import DomainReadOnlyAdmin

from .models import LeadScoreItem, ScoringRule, ScoringRun

for model in (ScoringRule, ScoringRun, LeadScoreItem):
    admin.site.register(model, DomainReadOnlyAdmin)
