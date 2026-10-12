import uuid
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.campaigns.models import Campaign, CampaignChannel
from apps.companies.enrichment import enqueue_enrichment
from apps.companies.models import Contact, WebsiteObservation
from apps.crm.models import Lead, Opportunity
from apps.crm.services import delete_opportunity, save_opportunity
from apps.jobs.models import Job
from apps.jobs.payloads import validate_payload
from apps.jobs.runner import JobRunner
from apps.scoring.engine import evaluate, validate_configuration
from apps.scoring.models import LeadScoreItem, ScoringRule, ScoringRun
from apps.scoring.selectors import input_snapshot
from apps.scoring.services import calculate_score, enqueue_score, initialize_rules, save_rule
from tests.test_phase1 import domain as domain

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def lock_path(tmp_path, monkeypatch):
    monkeypatch.setattr("apps.jobs.locking.worker_lock_path", lambda: tmp_path / "worker.lock")


def calculate(domain, key=None, lead=None):
    member = domain["member"]
    return calculate_score(
        user=member.user,
        workspace_id=member.workspace_id,
        lead_id=(lead or domain["lead"]).pk,
        idempotency_key=key or str(uuid.uuid4()),
    )


def init(domain):
    member = domain["member"]
    initialize_rules(user=member.user, workspace_id=member.workspace_id)


def update_rule(domain, code, **data):
    member = domain["member"]
    rule = ScoringRule.objects.get(workspace=member.workspace, code=code)
    return save_rule(
        user=member.user,
        workspace_id=member.workspace_id,
        pk=rule.pk,
        data=data,
        expected_version=rule.version,
    )


def test_score_is_reproducible_and_history_is_immutable(domain):
    domain["company"].google_review_count = 2
    domain["company"].save()
    first = calculate(domain, "same")
    assert first.score == 18  # low reviews + detected medium opportunity
    assert calculate(domain, "same").pk == first.pk
    second = calculate(domain)
    assert second.fingerprint == first.fingerprint and second.score == first.score
    domain["lead"].refresh_from_db()
    assert domain["lead"].current_score_run_id == second.pk
    assert first.items.count() == 9 and ScoringRun.objects.count() == 2
    assert Opportunity.objects.filter(is_active=True, scoring_run__isnull=False).count() == 1
    assert not first.opportunities.get().is_active
    for operation in (
        lambda: first.save(),
        lambda: first.delete(),
        lambda: first.items.all().delete(),
        lambda: first.items.all().update(points=99),
    ):
        with pytest.raises(ValidationError):
            operation()
    assert AuditEvent.objects.filter(action="SCORE_CALCULATED").count() == 2


def test_rules_can_be_disabled_without_rewriting_history(domain):
    company = domain["company"]
    company.website = "https://clinic.example.invalid/"
    company.save()
    first = calculate(domain)
    assert first.score == 10
    rule = update_rule(domain, "WEBSITE", enabled=False)
    assert rule.version == 2
    init(domain)
    rule.refresh_from_db()
    assert not rule.enabled
    second = calculate(domain)
    assert second.score == 0 and second.items.count() == 8
    assert first.items.get(rule_code="WEBSITE").points == 10
    assert first.rules["items"][0]["version"] == 1
    assert second.fingerprint != first.fingerprint


def test_weight_limits_and_empty_rule_set(domain):
    company = domain["company"]
    company.website = "https://clinic.example.invalid/"
    company.phone = "+34981000000"
    company.save()
    init(domain)
    update_rule(domain, "WEBSITE", points=100)
    update_rule(domain, "PHONE", points=100)
    run = calculate(domain)
    assert run.raw_score == 200 and run.score == 100
    update_rule(domain, "WEBSITE", points=-100)
    update_rule(domain, "PHONE", enabled=False)
    assert calculate(domain).score == 0
    for rule in ScoringRule.objects.filter(workspace=company.workspace):
        update_rule(domain, rule.code, enabled=False)
    empty = calculate(domain)
    assert empty.score == 0 and empty.items.count() == 0


def test_detectors_require_observations_and_preserve_manual_opportunities(domain):
    member, company = domain["member"], domain["company"]
    manual = Opportunity.objects.create(
        workspace=member.workspace,
        lead=domain["lead"],
        type="WEB",
        title="Manual",
        evidence="Manual evidence",
    )
    company.website = "https://clinic.example.invalid/"
    company.save()
    enqueue_enrichment(
        user=member.user,
        workspace_id=member.workspace_id,
        company_id=company.pk,
        idempotency_key="enrich",
    )
    assert JobRunner().run_once().status == "SUCCEEDED"
    observation = WebsiteObservation.objects.get()
    observation.has_contact_page = observation.has_booking = False
    observation.findings["contact_form_observed"] = False
    observation.save()
    run = calculate(domain)
    assert run.items.get(rule_code="WEAK_CTA").points == 10
    assert "Requiere revisión" in run.opportunities.get().evidence
    # The absence inference disappears after changing the website.
    company.website = "https://changed.example.invalid/"
    company.save()
    next_run = calculate(domain)
    assert next_run.items.get(rule_code="WEAK_CTA").points == 0
    manual.refresh_from_db()
    assert manual.is_active and manual.detected_by == "HUMAN"
    with pytest.raises(ValidationError):
        save_opportunity(
            user=member.user,
            workspace_id=member.workspace_id,
            pk=run.opportunities.get().pk,
            data={"title": "overwrite"},
        )
    with pytest.raises(ValidationError):
        delete_opportunity(
            user=member.user, workspace_id=member.workspace_id, pk=run.opportunities.get().pk
        )


def test_failed_or_unobserved_web_is_not_evidence_of_missing_cta(domain):
    assert input_snapshot(domain["lead"])["observation"] is None
    assert calculate(domain).items.get(rule_code="WEAK_CTA").points == 0


def test_missing_channel_requires_campaign_channel_and_relevant_data(domain):
    member, lead = domain["member"], domain["lead"]
    first = calculate(domain)
    assert first.items.get(rule_code="MISSING_CHANNEL").points == 0
    CampaignChannel.objects.create(
        workspace=member.workspace, campaign=lead.campaign, channel="EMAIL", enabled=True
    )
    run = calculate(domain)
    assert run.items.get(rule_code="MISSING_CHANNEL").points == 7
    Contact.objects.create(
        workspace=member.workspace,
        company=lead.company,
        full_name="Public",
        email="info@example.invalid",
        is_business_contact=False,
    )
    assert calculate(domain).items.get(rule_code="EMAIL").points == 0
    Contact.objects.filter(company=lead.company).update(is_business_contact=True)
    run = calculate(domain)
    assert run.items.get(rule_code="EMAIL").points == 12
    assert run.items.get(rule_code="MISSING_CHANNEL").points == 0


def test_scoring_is_scoped_and_keys_cannot_target_other_leads(domain):
    member = domain["member"]
    with pytest.raises(Http404):
        calculate(domain, lead=domain["other_lead"])
    first = calculate(domain, "key")
    campaign = Campaign.objects.create(workspace=member.workspace, name="Other campaign")
    other = Lead.objects.create(
        workspace=member.workspace, company=domain["company"], campaign=campaign
    )
    with pytest.raises(ValidationError):
        calculate(domain, "key", lead=other)
    assert first.lead_id == domain["lead"].pk
    member.role = "VIEWER"
    member.save()
    with pytest.raises(PermissionDenied):
        calculate(domain)


def test_rule_permissions_versions_and_validation(domain):
    init(domain)
    member = domain["member"]
    rule = update_rule(domain, "WEBSITE", points=11)
    assert rule.version == 2
    assert update_rule(domain, "WEBSITE", points=11).version == 2
    with pytest.raises(ValidationError):
        save_rule(
            user=member.user,
            workspace_id=member.workspace_id,
            pk=rule.pk,
            data={"points": 12},
            expected_version=1,
        )
    with pytest.raises(ValidationError):
        update_rule(domain, "WEBSITE", points=101)
    with pytest.raises(ValidationError):
        save_rule(
            user=member.user,
            workspace_id=member.workspace_id,
            pk=rule.pk,
            data={"code": "EMAIL"},
            expected_version=rule.version,
        )
    member.role = "MEMBER"
    member.save()
    with pytest.raises(PermissionDenied):
        update_rule(domain, "WEBSITE", enabled=False)
    assert calculate(domain).score == 0


@pytest.mark.parametrize(
    "code,configuration",
    [
        ("UNKNOWN", {}),
        ("WEBSITE", []),
        ("WEBSITE", {"execute": "code"}),
        ("LOW_REVIEWS", {"max_reviews": True}),
        ("LOW_REVIEWS", {"max_reviews": -1}),
        ("MISSING_CHANNEL", {"channel": "WHATSAPP"}),
    ],
)
def test_configuration_is_closed_and_bounded(code, configuration):
    with pytest.raises(ValidationError):
        validate_configuration(code, configuration)


def test_snapshot_can_reproduce_past_calculation_after_data_changes(domain):
    run = calculate(domain)
    company = domain["company"]
    company.website = "https://new.example.invalid/"
    company.save()
    score, items, _ = evaluate(run.inputs, run.rules["items"])
    assert score == run.score
    assert sum(item["points"] for item in items) == run.raw_score
    assert calculate(domain).score != run.score


def test_scoring_transaction_rolls_back_score_history_and_opportunities(domain):
    with patch("apps.scoring.services.record_event", side_effect=RuntimeError("audit unavailable")):
        with pytest.raises(RuntimeError):
            calculate(domain)
    assert not ScoringRun.objects.exists() and not LeadScoreItem.objects.exists()
    assert not ScoringRule.objects.exists()
    domain["lead"].refresh_from_db()
    assert domain["lead"].current_score_run_id is None


def test_job_recovery_and_revocation(domain):
    member, lead = domain["member"], domain["lead"]
    job = enqueue_score(
        user=member.user,
        workspace_id=member.workspace_id,
        lead_id=lead.pk,
        idempotency_key="job-key",
    )
    assert (
        enqueue_score(
            user=member.user,
            workspace_id=member.workspace_id,
            lead_id=lead.pk,
            idempotency_key="job-key",
        ).pk
        == job.pk
    )
    assert JobRunner().run_once().status == "SUCCEEDED"
    Job.objects.filter(pk=job.pk).update(status="PENDING")
    assert JobRunner().run_once().status == "SUCCEEDED"
    assert ScoringRun.objects.count() == 1
    enqueue_score(
        user=member.user,
        workspace_id=member.workspace_id,
        lead_id=lead.pk,
        idempotency_key="revoked",
    )
    member.is_active = False
    member.save()
    assert JobRunner().run_once().status == "FAILED"
    assert ScoringRun.objects.count() == 1


def test_archived_records_and_invalid_job_payloads_are_rejected(domain):
    for payload in ({}, {"lead_id": "bad"}, {"lead_id": str(domain["lead"].pk), "score": 100}):
        with pytest.raises(ValidationError):
            validate_payload(Job.Kind.SCORE_LEAD, payload)
    domain["lead"].is_archived = True
    domain["lead"].save()
    with pytest.raises(ValidationError):
        calculate(domain)


def test_ui_scoping_csrf_rules_and_breakdown(domain):
    member, lead = domain["member"], domain["lead"]
    client = Client(enforce_csrf_checks=True)
    url = reverse("scoring:lead", args=[member.workspace_id, lead.pk])
    assert client.get(url).status_code == 302
    client.force_login(member.user)
    assert client.get(url).status_code == 200
    key = str(uuid.uuid4())
    assert client.post(url, {"idempotency_key": key}).status_code == 403
    token = client.cookies["csrftoken"].value
    assert (
        client.post(url, {"idempotency_key": key, "csrfmiddlewaretoken": token}).status_code == 302
    )
    assert JobRunner().run_once().status == "SUCCEEDED"
    assert b"scoring-v1" in client.get(url).content
    current = ScoringRun.objects.get()
    assert client.get(url, {"run": current.pk}).status_code == 200
    assert client.get(url, {"run": uuid.uuid4()}).status_code == 404
    assert client.get(url, {"run": "invalid"}).status_code == 404
    assert (
        client.post(url, {"idempotency_key": "bad", "csrfmiddlewaretoken": token}).status_code
        == 400
    )
    rules_url = reverse("scoring:rules", args=[member.workspace_id])
    assert client.get(rules_url).status_code == 200
    assert client.post(rules_url, {"csrfmiddlewaretoken": token}).status_code == 302
    rule = ScoringRule.objects.get(workspace=member.workspace, code="WEBSITE")
    rule_url = reverse("scoring:rule", args=[member.workspace_id, rule.pk])
    assert client.get(rule_url).status_code == 200
    assert (
        client.post(
            rule_url,
            {
                "csrfmiddlewaretoken": token,
                "name": rule.name,
                "points": 15,
                "configuration": '{"execute":"anything"}',
                "sort_order": 0,
                "expected_version": rule.version,
            },
        ).status_code
        == 400
    )
    assert (
        client.post(
            rule_url,
            {
                "csrfmiddlewaretoken": token,
                "name": rule.name,
                "points": 15,
                "configuration": "{}",
                "sort_order": 0,
                "enabled": "on",
                "expected_version": rule.version,
            },
        ).status_code
        == 302
    )
    client.force_login(domain["foreign"].user)
    assert client.get(url).status_code == 404
    assert client.get(rule_url).status_code == 404
    client.force_login(member.user)
    member.role = "VIEWER"
    member.save()
    assert client.get(url).status_code == 200
    assert client.get(rule_url).status_code == 403
    assert (
        client.post(url, {"idempotency_key": key, "csrfmiddlewaretoken": token}).status_code == 403
    )


def test_history_foreign_relations_are_rejected(domain):
    run = calculate(domain)
    item = LeadScoreItem(
        workspace=domain["member"].workspace,
        lead=domain["other_lead"],
        scoring_run=run,
        rule_code="WEBSITE",
        points=1,
        explanation="Test",
        evidence="Test",
        rule_version=1,
        matched=True,
    )
    with pytest.raises(ValidationError):
        item.save()


def test_latest_failed_crawl_invalidates_previous_absence_inference(domain):
    from integrations.websites.errors import CrawlError

    member, company = domain["member"], domain["company"]
    company.website = "https://clinic.example.invalid/"
    company.save()
    enqueue_enrichment(
        user=member.user,
        workspace_id=member.workspace_id,
        company_id=company.pk,
        idempotency_key="first-crawl",
    )
    JobRunner().run_once()
    obs = WebsiteObservation.objects.get()
    obs.has_contact_page = obs.has_booking = False
    obs.findings["contact_form_observed"] = False
    obs.save()
    assert calculate(domain).items.get(rule_code="WEAK_CTA").matched
    enqueue_enrichment(
        user=member.user,
        workspace_id=member.workspace_id,
        company_id=company.pk,
        idempotency_key="failed-crawl",
    )
    with patch("integrations.websites.mock.mock_crawl", side_effect=CrawlError("ROBOTS_DENIED")):
        assert JobRunner().run_once().status == "FAILED"
    assert not calculate(domain).items.get(rule_code="WEAK_CTA").matched


def test_invalid_idempotency_keys_and_stale_execution(domain):
    from apps.jobs.execution import StaleExecution
    from apps.jobs.handlers import ExecutionContext
    from apps.scoring.services import run_score

    with pytest.raises(ValidationError):
        calculate(domain, " ")
    member = domain["member"]
    job = enqueue_score(
        user=member.user,
        workspace_id=member.workspace_id,
        lead_id=domain["lead"].pk,
        idempotency_key="queued",
    )
    context = ExecutionContext(
        job_id=job.pk,
        workspace_id=member.workspace_id,
        attempt=1,
        payload=job.payload,
        progress=lambda *args: None,
    )
    with pytest.raises(StaleExecution):
        run_score(context)
    assert not ScoringRun.objects.exists()
