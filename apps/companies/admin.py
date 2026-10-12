from django.contrib import admin

from common.admin import DomainReadOnlyAdmin

from .models import Company, CompanyExternalRef, Contact, SocialProfile, WebsiteObservation

for model in (Company, CompanyExternalRef, Contact, SocialProfile, WebsiteObservation):
    admin.site.register(model, DomainReadOnlyAdmin)
