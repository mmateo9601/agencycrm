from django.contrib import admin

from common.admin import DomainReadOnlyAdmin

from .models import Proposal, ProposalAttachment, SalesAsset

for model in (Proposal, ProposalAttachment, SalesAsset):
    admin.site.register(model, DomainReadOnlyAdmin)
