from django.contrib import admin

from common.admin import DomainReadOnlyAdmin

from .models import Campaign, CampaignChannel, CampaignService

for model in (Campaign, CampaignChannel, CampaignService):
    admin.site.register(model, DomainReadOnlyAdmin)
