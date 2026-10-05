from django.contrib import admin

from common.admin import DomainReadOnlyAdmin

from .models import Company, CompanyExternalRef, Contact, SocialProfile

for model in (Company, CompanyExternalRef, Contact, SocialProfile):
    admin.site.register(model, DomainReadOnlyAdmin)
