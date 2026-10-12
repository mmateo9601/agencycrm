"""Explicit HTTP resources; arbitrary model names are never accepted from clients."""

from dataclasses import dataclass
from functools import partial

from apps.campaigns.forms import CampaignForm, ChannelForm
from apps.campaigns.models import Campaign, CampaignChannel
from apps.campaigns.selectors import campaign_records
from apps.campaigns.services import archive_campaign, save_campaign, save_channel
from apps.companies.forms import CompanyForm, ContactForm, ExternalRefForm, SocialProfileForm
from apps.companies.models import Company, CompanyExternalRef, Contact, SocialProfile
from apps.companies.selectors import company_records
from apps.companies.services import (
    archive_company,
    save_company,
    save_contact,
    save_external_ref,
    save_social_profile,
)
from apps.crm.forms import LeadForm, OpportunityForm
from apps.crm.models import Lead, Opportunity
from apps.crm.selectors import crm_records
from apps.crm.services import archive_lead, delete_opportunity, save_lead, save_opportunity
from common.services import delete_record


@dataclass(frozen=True)
class Resource:
    key: str
    label: str
    model: type
    form: type
    save: object
    select: object
    delete: object
    delete_label: str = "Eliminar"


RESOURCES = {
    item.key: item
    for item in (
        Resource(
            "companies",
            "Empresas",
            Company,
            CompanyForm,
            save_company,
            company_records,
            archive_company,
            "Archivar",
        ),
        Resource(
            "campaigns",
            "Campañas",
            Campaign,
            CampaignForm,
            save_campaign,
            campaign_records,
            archive_campaign,
            "Archivar",
        ),
        Resource(
            "leads", "Leads", Lead, LeadForm, save_lead, crm_records, archive_lead, "Archivar"
        ),
        Resource(
            "contacts",
            "Contactos",
            Contact,
            ContactForm,
            save_contact,
            company_records,
            partial(delete_record, model=Contact),
        ),
        Resource(
            "opportunities",
            "Oportunidades",
            Opportunity,
            OpportunityForm,
            save_opportunity,
            crm_records,
            delete_opportunity,
        ),
        Resource(
            "sources",
            "Referencias externas",
            CompanyExternalRef,
            ExternalRefForm,
            save_external_ref,
            company_records,
            partial(delete_record, model=CompanyExternalRef),
        ),
        Resource(
            "social",
            "Perfiles sociales",
            SocialProfile,
            SocialProfileForm,
            save_social_profile,
            company_records,
            partial(delete_record, model=SocialProfile),
        ),
        Resource(
            "channels",
            "Canales de campaña",
            CampaignChannel,
            ChannelForm,
            save_channel,
            campaign_records,
            partial(delete_record, model=CampaignChannel),
        ),
    )
}
