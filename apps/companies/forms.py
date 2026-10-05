from common.forms import ScopedModelForm

from .models import Company, CompanyExternalRef, Contact, SocialProfile
from .schema import COMPANY_FIELDS, CONTACT_FIELDS, EXTERNAL_REF_FIELDS, SOCIAL_FIELDS


class CompanyForm(ScopedModelForm):
    class Meta:
        model = Company
        fields = COMPANY_FIELDS


class ContactForm(ScopedModelForm):
    class Meta:
        model = Contact
        fields = CONTACT_FIELDS


class ExternalRefForm(ScopedModelForm):
    class Meta:
        model = CompanyExternalRef
        fields = EXTERNAL_REF_FIELDS


class SocialProfileForm(ScopedModelForm):
    class Meta:
        model = SocialProfile
        fields = SOCIAL_FIELDS
