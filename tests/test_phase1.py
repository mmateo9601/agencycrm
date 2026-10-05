from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.campaigns.models import Campaign, CampaignChannel, CampaignService
from apps.campaigns.services import change_campaign_status, save_campaign
from apps.companies.models import Company, CompanyExternalRef, Contact, SocialProfile
from apps.companies.services import save_company, save_contact, save_external_ref
from apps.crm.models import Lead, Opportunity
from apps.crm.services import LeadStateService, save_lead, save_opportunity
from common.normalization import (
    canonicalize_url,
    normalize_company_name,
    normalize_domain,
    normalize_email,
    normalize_phone,
)
from common.resources import RESOURCES
from tests.factories import MembershipFactory, ServiceOfferingFactory, UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def domain():
    member = MembershipFactory(role="OWNER")
    foreign = MembershipFactory(role="OWNER")
    company = save_company(
        user=member.user,
        workspace_id=member.workspace_id,
        data={"name": "Clínica Norte", "city": "A Coruña", "primary_sector": "Dentistas"},
    )
    campaign = save_campaign(
        user=member.user, workspace_id=member.workspace_id, data={"name": "Dentistas Galicia"}
    )
    lead = save_lead(
        user=member.user,
        workspace_id=member.workspace_id,
        data={"company": company, "campaign": campaign},
    )
    other_company = save_company(
        user=foreign.user, workspace_id=foreign.workspace_id, data={"name": "Secreto ajeno"}
    )
    other_campaign = save_campaign(
        user=foreign.user, workspace_id=foreign.workspace_id, data={"name": "Campaña ajena"}
    )
    other_lead = save_lead(
        user=foreign.user,
        workspace_id=foreign.workspace_id,
        data={"company": other_company, "campaign": other_campaign},
    )
    return {
        "member": member,
        "foreign": foreign,
        "company": company,
        "campaign": campaign,
        "lead": lead,
        "other_company": other_company,
        "other_campaign": other_campaign,
        "other_lead": other_lead,
    }


def url(domain, resource, action="list", obj=None, workspace=None):
    kwargs = {"workspace_id": workspace or domain["member"].workspace_id, "resource": resource}
    if obj:
        kwargs["pk"] = obj.pk
    return reverse(f"workspace:{action}", kwargs=kwargs)


def payload(domain, resource):
    company, campaign, lead = domain["company"], domain["campaign"], domain["lead"]
    return {
        "companies": {"name": "Nueva clínica", "country_code": "ES", "city": "Vigo"},
        "campaigns": {
            "name": "Nueva campaña",
            "target_country_code": "ES",
            "desired_leads": 10,
            "minimum_score": 0,
            "require_manual_approval": "on",
        },
        "leads": {
            "company": str(company.pk),
            "campaign": str(campaign.pk),
            "notes": "Seguimiento manual",
        },
        "contacts": {
            "company": str(company.pk),
            "full_name": "Contacto demo",
            "contact_type": "GENERAL",
            "observed_at": "2026-10-05T10:00",
            "email": "info@example.com",
            "is_business_contact": "on",
        },
        "sources": {
            "company": str(company.pk),
            "provider": "MANUAL_IMPORT",
            "external_id": "demo-1",
            "observed_at": "2026-10-05T10:00",
            "metadata": "{}",
        },
        "social": {
            "company": str(company.pk),
            "platform": "LINKEDIN",
            "url": "https://example.com/clinica",
            "observed_at": "2026-10-05T10:00",
            "metadata": "{}",
        },
        "channels": {
            "campaign": str(campaign.pk),
            "channel": "EMAIL",
            "enabled": "on",
            "priority": 1,
            "max_attempts": 2,
            "cooldown_days": 14,
        },
        "opportunities": {
            "lead": str(lead.pk),
            "type": "SEO_LOCAL",
            "severity": "HIGH",
            "title": "Mejorar SEO local",
            "evidence": "Observación manual ficticia",
            "confidence": "0.900",
            "is_active": "on",
        },
    }[resource]


def test_ui_campaign_company_lead_and_history(client, domain):
    client.force_login(domain["member"].user)
    service = ServiceOfferingFactory(workspace=domain["member"].workspace)
    data = payload(domain, "campaigns") | {"services": [str(service.pk)]}
    response = client.post(url(domain, "campaigns", "create"), data)
    assert response.status_code == 302
    campaign = Campaign.objects.get(name="Nueva campaña")
    assert list(campaign.services.all()) == [service]
    assert campaign.created_by == domain["member"].user
    response = client.post(url(domain, "companies", "create"), payload(domain, "companies"))
    assert response.status_code == 302
    company = Company.objects.get(name="Nueva clínica")
    data = {"company": str(company.pk), "campaign": str(campaign.pk)}
    response = client.post(url(domain, "leads", "create"), data)
    assert response.status_code == 302
    lead = Lead.objects.get(company=company, campaign=campaign)
    assert lead.status == "NEW" and lead.status_history.count() == 1
    response = client.post(
        url(domain, "leads", "state", lead),
        {"target": "QUALIFIED", "reason": "Revisión manual", "expected_revision": 0},
    )
    assert response.status_code == 302
    lead.refresh_from_db()
    assert lead.status == "QUALIFIED"
    response = client.get(url(domain, "leads", "detail", lead))
    assert response.status_code == 200
    assert "Revisión manual" in response.content.decode()
    assert client.post(url(domain, "leads", "create"), data).status_code == 400
    assert Lead.objects.filter(company=company, campaign=campaign).count() == 1


@pytest.mark.parametrize("resource", list(RESOURCES))
def test_crud_all_resources(client, domain, resource):
    client.force_login(domain["member"].user)
    data = payload(domain, resource)
    if resource == "leads":
        company = Company.objects.create(workspace=domain["member"].workspace, name="Otra clínica")
        data["company"] = str(company.pk)
    assert client.get(url(domain, resource)).status_code == 200
    assert client.get(url(domain, resource, "create")).status_code == 200
    response = client.post(url(domain, resource, "create"), data)
    assert response.status_code == 302, response.content.decode()[:3000]
    obj = (
        RESOURCES[resource]
        .model.objects.filter(workspace=domain["member"].workspace)
        .latest("created_at")
    )
    assert client.get(url(domain, resource, "detail", obj)).status_code == 200
    assert client.get(url(domain, resource, "edit", obj)).status_code == 200
    assert client.post(url(domain, resource, "edit", obj), data).status_code == 302
    assert client.get(url(domain, resource, "delete", obj)).status_code == 200
    assert client.post(url(domain, resource, "delete", obj)).status_code == 302
    if resource in {"companies", "campaigns", "leads"}:
        obj.refresh_from_db()
        assert (
            not obj.is_active
            if resource == "companies"
            else obj.status == "ARCHIVED"
            if resource == "campaigns"
            else obj.is_archived
        )
    else:
        assert not type(obj).objects.filter(pk=obj.pk).exists()


@pytest.mark.parametrize("resource", list(RESOURCES))
def test_workspace_urls_and_foreign_ids_are_isolated(client, domain, resource):
    client.force_login(domain["foreign"].user)
    for action in ("list", "create"):
        assert client.get(url(domain, resource, action)).status_code == 404
        assert client.post(
            url(domain, resource, action), payload(domain, resource)
        ).status_code in (404, 405)
    client.force_login(domain["member"].user)
    assert b"Secreto ajeno" not in client.get(url(domain, resource)).content
    if resource in {"companies", "campaigns", "leads"}:
        foreign = domain[
            {"companies": "other_company", "campaigns": "other_campaign", "leads": "other_lead"}[
                resource
            ]
        ]
        for action in ("detail", "edit", "delete", "state"):
            assert client.post(
                url(domain, resource, action, foreign), payload(domain, resource)
            ).status_code in (404, 405)
            if action != "state":
                assert client.get(url(domain, resource, action, foreign)).status_code == 404


@pytest.mark.parametrize(
    "resource,field,other",
    [
        ("contacts", "company", "other_company"),
        ("sources", "company", "other_company"),
        ("social", "company", "other_company"),
        ("channels", "campaign", "other_campaign"),
        ("leads", "company", "other_company"),
        ("leads", "campaign", "other_campaign"),
        ("opportunities", "lead", "other_lead"),
    ],
)
def test_forged_relations_rejected_by_forms_and_services(client, domain, resource, field, other):
    client.force_login(domain["member"].user)
    data = payload(domain, resource) | {field: str(domain[other].pk)}
    before = RESOURCES[resource].model.objects.count()
    assert client.post(url(domain, resource, "create"), data).status_code == 400
    assert RESOURCES[resource].model.objects.count() == before
    with pytest.raises(Http404):
        RESOURCES[resource].save(
            user=domain["member"].user,
            workspace_id=domain["member"].workspace_id,
            data={field: domain[other]},
        )


def test_services_and_assignees_must_be_in_workspace(client, domain):
    user, workspace = domain["member"].user, domain["member"].workspace
    client.force_login(user)
    alien_service = ServiceOfferingFactory(workspace=domain["foreign"].workspace)
    for resource, field, value in (
        ("campaigns", "services", [str(alien_service.pk)]),
        ("leads", "recommended_service", str(alien_service.pk)),
        ("leads", "assigned_to", str(domain["foreign"].user_id)),
    ):
        assert (
            client.post(
                url(domain, resource, "create"), payload(domain, resource) | {field: value}
            ).status_code
            == 400
        )
    with pytest.raises(Http404):
        save_campaign(
            user=user, workspace_id=workspace.pk, data={"name": "Bad", "services": [alien_service]}
        )
    assert not Campaign.objects.filter(name="Bad").exists()
    with pytest.raises(ValidationError):
        CampaignService.objects.create(
            workspace=workspace, campaign=domain["campaign"], service=alien_service
        )


@pytest.mark.parametrize("inactive", ["user", "workspace", "membership"])
def test_revoked_access_blocks_existing_sessions(client, domain, inactive):
    member = domain["member"]
    client.force_login(member.user)
    obj = {"user": member.user, "workspace": member.workspace, "membership": member}[inactive]
    obj.is_active = False
    obj.save()
    assert client.get(url(domain, "companies")).status_code == (302 if inactive == "user" else 404)
    with pytest.raises(Http404):
        save_company(user=member.user, workspace_id=member.workspace_id, data={"name": "Bad"})


@pytest.mark.parametrize("resource", list(RESOURCES))
def test_viewer_is_read_only(client, domain, resource):
    member = domain["member"]
    member.role = "VIEWER"
    member.save()
    client.force_login(member.user)
    assert client.get(url(domain, resource)).status_code == 200
    assert client.get(url(domain, resource, "create")).status_code == 403
    assert (
        client.post(url(domain, resource, "create"), payload(domain, resource)).status_code == 403
    )
    with pytest.raises(PermissionDenied):
        RESOURCES[resource].save(user=member.user, workspace_id=member.workspace_id, data={})


def test_csrf_required_for_domain_mutations(domain):
    client = Client(enforce_csrf_checks=True)
    client.force_login(domain["member"].user)
    assert (
        client.post(url(domain, "companies", "create"), payload(domain, "companies")).status_code
        == 403
    )
    client.get(url(domain, "companies", "create"))
    token = client.cookies["csrftoken"].value
    assert (
        client.post(
            url(domain, "companies", "create"),
            payload(domain, "companies") | {"csrfmiddlewaretoken": token},
        ).status_code
        == 302
    )
    for action in ("delete", "state"):
        assert (
            client.post(
                url(domain, "leads", action, domain["lead"]), {"target": "QUALIFIED"}
            ).status_code
            == 403
        )


def test_lead_state_service_idempotency_revisions_and_history(domain):
    args = {
        "user": domain["member"].user,
        "workspace_id": domain["member"].workspace_id,
        "pk": domain["lead"].pk,
    }
    with pytest.raises(ValidationError):
        LeadStateService.transition(**args, target="WON")
    lead = LeadStateService.transition(**args, target="QUALIFIED", expected_revision=0)
    assert lead.status_history.count() == 2
    LeadStateService.transition(**args, target="QUALIFIED", expected_revision=0)
    assert lead.status_history.count() == 2
    with pytest.raises(ValidationError):
        LeadStateService.transition(**args, target="READY", expected_revision=0)
    for target in ("READY", "CONTACTED", "REPLIED", "MEETING", "PROPOSAL", "WON"):
        lead = LeadStateService.transition(**args, target=target)
    assert lead.last_contact_at is not None and lead.status_revision == 7
    with pytest.raises(ValidationError):
        LeadStateService.transition(**args, target="NEW")
    assert AuditEvent.objects.filter(object_id=lead.pk, action="LEAD_STATUS_CHANGED").count() == 7
    lead.status = "LOST"
    with pytest.raises(ValidationError):
        lead.save()


def test_state_change_rolls_back_when_history_or_audit_fails(domain):
    args = {
        "user": domain["member"].user,
        "workspace_id": domain["member"].workspace_id,
        "pk": domain["lead"].pk,
    }
    for method in (
        "apps.crm.services.LeadStatusHistory.objects.create",
        "apps.crm.services.record_event",
    ):
        with patch(method, side_effect=RuntimeError("test rollback")), pytest.raises(RuntimeError):
            LeadStateService.transition(**args, target="QUALIFIED")
        domain["lead"].refresh_from_db()
        assert domain["lead"].status == "NEW"
        assert domain["lead"].status_history.count() == 1


def test_history_and_audit_are_append_only(domain):
    for obj in (domain["lead"].status_history.first(), AuditEvent.objects.first()):
        with pytest.raises(ValidationError):
            obj.save()
        with pytest.raises(ValidationError):
            obj.delete()
        with pytest.raises(ValidationError):
            type(obj).objects.filter(pk=obj.pk).delete()
        with pytest.raises(ValidationError):
            type(obj).objects.filter(pk=obj.pk).update(created_at=obj.created_at)


def test_database_uniqueness_and_ranges(domain):
    lead = domain["lead"]
    with pytest.raises(IntegrityError), transaction.atomic():
        Lead.objects.bulk_create(
            [Lead(workspace=lead.workspace, campaign=lead.campaign, company=lead.company)]
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        Lead.objects.filter(pk=lead.pk).update(score=101)
    with pytest.raises(IntegrityError), transaction.atomic():
        Campaign.objects.filter(pk=lead.campaign_id).update(minimum_score=101)
    with pytest.raises(IntegrityError), transaction.atomic():
        Company.objects.filter(pk=lead.company_id).update(latitude=Decimal("91"))
    common = {
        "workspace": lead.workspace,
        "company": lead.company,
        "provider": "GOOGLE_PLACES",
        "external_id": "same",
    }
    CompanyExternalRef.objects.create(**common)
    another = Company.objects.create(workspace=lead.workspace, name="Otra")
    with pytest.raises(IntegrityError), transaction.atomic():
        CompanyExternalRef.objects.bulk_create(
            [CompanyExternalRef(**(common | {"company": another}))]
        )
    CompanyExternalRef.objects.create(
        **(common | {"workspace": domain["foreign"].workspace, "company": domain["other_company"]})
    )
    channel = CampaignChannel.objects.create(
        workspace=lead.workspace, campaign=lead.campaign, channel="EMAIL"
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        CampaignChannel.objects.bulk_create(
            [
                CampaignChannel(
                    workspace=lead.workspace, campaign=lead.campaign, channel=channel.channel
                )
            ]
        )


def test_normalization_and_no_automatic_merge(domain):
    assert normalize_company_name("  CLÍNICA   Norte  ") == "clinica norte"
    assert normalize_email(" INFO@EXAMPLE.COM ") == "info@example.com"
    assert normalize_domain("https://WWW.Example.COM/path?q=1#x") == "example.com"
    assert canonicalize_url("HTTPS://EXAMPLE.COM:443/a#x") == "https://example.com/a"
    assert normalize_phone("+34 981 123 456") == "+34981123456"
    assert normalize_phone("") == ""
    for bad in (
        "javascript:alert(1)",
        "https://user:password@example.com",
        "https://example.com:bad",
    ):
        with pytest.raises(ValidationError):
            canonicalize_url(bad)
    for bad in ("invalid", "123"):
        with pytest.raises(ValidationError):
            normalize_phone(bad)
    company = save_company(
        user=domain["member"].user,
        workspace_id=domain["member"].workspace_id,
        data={
            "name": "Clínica Norte",
            "website": "https://www.example.com/",
            "phone": "+34 981 123 456",
        },
    )
    assert company.pk != domain["company"].pk
    assert company.normalized_domain == "example.com"
    contact = save_contact(
        user=domain["member"].user,
        workspace_id=domain["member"].workspace_id,
        data={
            "company": company,
            "full_name": "Demo",
            "phone": company.phone,
            "email": "INFO@example.com",
        },
    )
    assert contact.whatsapp_e164 == "" and contact.phone_e164 == "+34981123456"


def test_filters_pagination_and_escaping(client, domain):
    client.force_login(domain["member"].user)
    Contact.objects.create(
        workspace=domain["member"].workspace,
        company=domain["company"],
        full_name="Demo",
        email="demo@example.com",
    )
    Contact.objects.create(
        workspace=domain["member"].workspace,
        company=domain["company"],
        full_name="Demo 2",
        email="demo2@example.com",
    )
    for filters in (
        {"city": "Coruña", "sector": "Dentistas"},
        {"has_email": "yes"},
        {"campaign": str(domain["campaign"].pk)},
        {"min_score": 0, "max_score": 0},
    ):
        response = client.get(url(domain, "leads"), filters)
        assert response.status_code == 200 and response.context["page_obj"].paginator.count == 1
    assert (
        client.get(url(domain, "leads"), {"has_email": "no"}).context["page_obj"].paginator.count
        == 0
    )
    response = client.get(url(domain, "leads"), {"campaign": str(domain["other_campaign"].pk)})
    assert (
        response.context["filter_form"].errors and response.context["page_obj"].paginator.count == 0
    )
    for filters in (
        {"min_score": "bad"},
        {"min_score": 50, "max_score": 20},
        {"status": "INVALID"},
    ):
        assert client.get(url(domain, "leads"), filters).context["filter_form"].errors
    for n in range(26):
        Company.objects.create(workspace=domain["member"].workspace, name=f"Demo {n}")
    response = client.get(url(domain, "companies"))
    assert len(response.context["page_obj"]) == 25 and response.context["page_obj"].has_next()
    company = save_company(
        user=domain["member"].user,
        workspace_id=domain["member"].workspace_id,
        data={"name": "<script>alert(1)</script>"},
    )
    content = client.get(url(domain, "companies", "detail", company)).content.decode()
    assert "<script>alert(1)</script>" not in content and "&lt;script&gt;" in content


def test_admin_cannot_bypass_domain_services(client, domain):
    user = UserFactory(is_superuser=True, is_staff=True)
    client.force_login(user)
    for obj in (
        domain["company"],
        domain["campaign"],
        domain["lead"],
        domain["lead"].status_history.first(),
        AuditEvent.objects.first(),
    ):
        prefix = f"admin:{obj._meta.app_label}_{obj._meta.model_name}"
        assert client.get(reverse(f"{prefix}_change", args=[obj.pk])).status_code == 200
        assert (
            client.post(reverse(f"{prefix}_change", args=[obj.pk]), {"status": "WON"}).status_code
            == 403
        )
        assert (
            client.post(reverse(f"{prefix}_delete", args=[obj.pk]), {"post": "yes"}).status_code
            == 403
        )
    assert client.get(url(domain, "companies")).status_code == 404


def test_partial_campaign_edit_keeps_services_and_rejects_illegal_states(client, domain):
    member = domain["member"]
    args = {"user": member.user, "workspace_id": member.workspace_id, "pk": domain["campaign"].pk}
    service = ServiceOfferingFactory(workspace=member.workspace)
    campaign = save_campaign(**args, data={"services": [service]})
    save_campaign(**args, data={"name": "Nombre actualizado"})
    assert list(campaign.services.all()) == [service]
    client.force_login(member.user)
    for target in ("READY", "DRAFT"):
        response = client.post(url(domain, "campaigns", "state", campaign), {"target": target})
        assert response.status_code == 302
        campaign.refresh_from_db()
        assert campaign.status == target
    assert (
        client.post(url(domain, "campaigns", "state", campaign), {"target": "RUNNING"}).status_code
        == 400
    )
    assert change_campaign_status(**args, target="DRAFT").status == "DRAFT"
    campaign.status = "RUNNING"
    with pytest.raises(ValidationError):
        campaign.save()


def test_archival_preserves_history_and_blocks_new_leads_and_transitions(client, domain):
    client.force_login(domain["member"].user)
    args = {"user": domain["member"].user, "workspace_id": domain["member"].workspace_id}
    response = client.post(
        url(domain, "leads", "state", domain["lead"]), {"target": "LOST", "expected_revision": 0}
    )
    assert response.status_code == 400
    lost = LeadStateService.transition(
        **args, pk=domain["lead"].pk, target="LOST", reason="Sin interés"
    )
    assert lost.lost_reason == "Sin interés"
    assert client.post(url(domain, "leads", "delete", lost)).status_code == 302
    assert lost.status_history.count() == 2
    assert client.get(url(domain, "leads")).context["page_obj"].paginator.count == 0
    assert (
        client.get(url(domain, "leads"), {"archived": "yes"}).context["page_obj"].paginator.count
        == 1
    )
    assert client.get(url(domain, "leads", "detail", lost)).status_code == 200
    assert client.post(url(domain, "companies", "delete", domain["company"])).status_code == 302
    new_campaign = save_campaign(**args, data={"name": "Nueva"})
    with pytest.raises(ValidationError):
        save_lead(**args, data={"company": domain["company"], "campaign": new_campaign})
    assert not Lead.objects.filter(campaign=new_campaign).exists()


def test_stale_transition_fails_without_history(domain):
    args = {
        "user": domain["member"].user,
        "workspace_id": domain["member"].workspace_id,
        "pk": domain["lead"].pk,
    }
    with (
        patch("django.db.models.query.QuerySet.update", return_value=0),
        pytest.raises(ValidationError),
    ):
        LeadStateService.transition(**args, target="QUALIFIED")
    domain["lead"].refresh_from_db()
    assert domain["lead"].status == "NEW" and domain["lead"].status_history.count() == 1


def test_direct_model_validation_blocks_foreign_relations_and_moving_records(domain):
    own = domain["member"].workspace
    foreign = domain["foreign"].workspace
    bad_objects = [
        Contact(workspace=own, company=domain["other_company"], full_name="Bad"),
        Lead(workspace=own, company=domain["company"], campaign=domain["other_campaign"]),
        CampaignChannel(workspace=own, campaign=domain["other_campaign"], channel="EMAIL"),
        Opportunity(
            workspace=own, lead=domain["other_lead"], type="WEB", title="Bad", evidence="Bad"
        ),
    ]
    for obj in bad_objects:
        with pytest.raises(ValidationError):
            obj.save()
    domain["company"].workspace = foreign
    with pytest.raises(ValidationError):
        domain["company"].save()
    domain["lead"].company = Company.objects.create(workspace=own, name="Different")
    with pytest.raises(ValidationError):
        domain["lead"].save()


def test_services_reject_mass_assignment_and_normalize_provider_keys(domain):
    args = {"user": domain["member"].user, "workspace_id": domain["member"].workspace_id}
    for forbidden in (
        {"workspace": domain["foreign"].workspace},
        {"status": "WON"},
        {"score": 100},
    ):
        with pytest.raises(ValidationError):
            save_lead(**args, pk=domain["lead"].pk, data=forbidden)
    ref = save_external_ref(
        **args,
        data={"company": domain["company"], "provider": " google_places ", "external_id": " demo "},
    )
    assert ref.provider == "GOOGLE_PLACES" and ref.external_id == "demo"
    with pytest.raises(ValidationError):
        save_external_ref(
            **args,
            data={"company": domain["company"], "provider": "GOOGLE_PLACES", "external_id": "demo"},
        )
    for metadata in ([1, 2], {"oversize": "x" * 16001}):
        with pytest.raises(ValidationError):
            save_external_ref(
                **args,
                data={
                    "company": domain["company"],
                    "provider": "MANUAL_IMPORT",
                    "external_id": "bad-json",
                    "metadata": metadata,
                },
            )


def test_children_cannot_be_read_or_changed_through_another_workspace(client, domain):
    member = domain["member"]
    foreign = domain["foreign"]
    MembershipFactory(workspace=foreign.workspace, user=member.user)
    client.force_login(member.user)
    foreign_objects = {
        "contacts": Contact.objects.create(
            workspace=foreign.workspace, company=domain["other_company"], full_name="Privado"
        ),
        "sources": CompanyExternalRef.objects.create(
            workspace=foreign.workspace,
            company=domain["other_company"],
            provider="MANUAL_IMPORT",
            external_id="private",
        ),
        "social": SocialProfile.objects.create(
            workspace=foreign.workspace,
            company=domain["other_company"],
            platform="X",
            url="https://example.com/private",
        ),
        "channels": CampaignChannel.objects.create(
            workspace=foreign.workspace, campaign=domain["other_campaign"], channel="EMAIL"
        ),
        "opportunities": Opportunity.objects.create(
            workspace=foreign.workspace,
            lead=domain["other_lead"],
            type="WEB",
            title="Privada",
            evidence="Prueba",
        ),
    }
    for resource, obj in foreign_objects.items():
        assert (
            client.get(
                url(domain, resource, "detail", obj, workspace=foreign.workspace_id)
            ).status_code
            == 200
        )
        for action in ("detail", "edit", "delete"):
            assert client.get(url(domain, resource, action, obj)).status_code == 404
        assert (
            client.post(url(domain, resource, "edit", obj), payload(domain, resource)).status_code
            == 404
        )
        assert client.post(url(domain, resource, "delete", obj)).status_code == 404
        assert type(obj).objects.filter(pk=obj.pk).exists()


def test_anonymous_unknown_resource_and_staff_deletion(client, domain):
    assert client.get(url(domain, "companies")).status_code == 302
    member = domain["member"]
    member.role = "SALES"
    member.save()
    client.force_login(member.user)
    assert client.get(url(domain, "not-a-model")).status_code == 404
    assert client.get(url(domain, "companies", "delete", domain["company"])).status_code == 403
    assert client.post(url(domain, "companies", "delete", domain["company"])).status_code == 403
    assert client.post(url(domain, "companies", "state", domain["company"])).status_code == 404


def test_phone_opportunity_and_assignment_filters(client, domain):
    member = domain["member"]
    args = {"user": member.user, "workspace_id": member.workspace_id}
    client.force_login(member.user)
    save_company(**args, pk=domain["company"].pk, data={"phone": "+34981123456"})
    save_lead(**args, pk=domain["lead"].pk, data={"assigned_to": member.user})
    save_opportunity(
        **args,
        data={
            "lead": domain["lead"],
            "type": "WEB",
            "title": "Mejorar web",
            "evidence": "Observación ficticia",
        },
    )
    for filters in (
        {"has_phone": "yes"},
        {"has_opportunity": "yes"},
        {"assigned_to": str(member.user_id)},
        {"q": "Clínica"},
    ):
        response = client.get(url(domain, "leads"), filters)
        assert response.status_code == 200 and response.context["page_obj"].paginator.count == 1
    assert (
        client.get(url(domain, "leads"), {"has_phone": "no"}).context["page_obj"].paginator.count
        == 0
    )
    assert (
        client.get(url(domain, "leads"), {"has_opportunity": "no"})
        .context["page_obj"]
        .paginator.count
        == 0
    )
