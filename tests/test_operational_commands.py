import io
import json
import uuid
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse

from apps.accounts.models import Membership
from apps.audit.models import AuditEvent
from apps.companies.models import Company
from apps.crm.models import Lead
from apps.dashboard.selectors import dashboard_for_user
from apps.dashboard.services import run_metrics
from apps.jobs.execution import StaleExecution, claim_next
from apps.jobs.handlers import ExecutionContext
from apps.jobs.locking import WorkerLock
from apps.jobs.models import Job
from apps.jobs.payloads import validate_payload
from apps.jobs.runner import JobRunner
from apps.jobs.services import JobService
from apps.scoring.batches import run_rebuild
from apps.scoring.models import ScoringRun
from tests.factories import UserFactory
from tests.test_phase1 import domain as domain

pytestmark = pytest.mark.django_db


@pytest.fixture
def operations(domain, tmp_path, monkeypatch):
    monkeypatch.setattr("apps.jobs.locking.worker_lock_path", lambda: tmp_path / "worker.lock")
    return domain


def enqueue(env, *, kind="REBUILD_SCORES", key=None, after=None, limit=100, campaign=None):
    member = env["member"]
    return JobService.enqueue(
        user=member.user,
        workspace_id=member.workspace_id,
        kind=kind,
        payload={
            "campaign_id": str((campaign or env["campaign"]).pk),
            "after": after,
            "limit": limit,
        }
        if kind == "REBUILD_SCORES"
        else {},
        idempotency_key=key or str(uuid.uuid4()),
    )


def cli(env, name, key=None, **extra):
    output = io.StringIO()
    call_command(
        name,
        workspace=env["member"].workspace_id,
        actor=env["member"].user_id,
        idempotency_key=key or uuid.uuid4(),
        stdout=output,
        **extra,
    )
    return output.getvalue()


def add_lead(env, *, archived=False, active=True):
    company = Company.objects.create(
        workspace=env["member"].workspace, name="Fictional batch company", is_active=active
    )
    return Lead.objects.create(
        workspace=env["member"].workspace,
        campaign=env["campaign"],
        company=company,
        is_archived=archived,
    )


def test_commands_only_enqueue_and_replay_same_key(operations):
    key = uuid.uuid4()
    with patch("apps.jobs.runner.JobRunner.run_once", side_effect=AssertionError("inline worker")):
        first = cli(operations, "rebuild_scores", key=key, campaign=operations["campaign"].pk)
        second = cli(operations, "rebuild_scores", key=key, campaign=operations["campaign"].pk)
        metric_key = uuid.uuid4()
        metrics = cli(operations, "refresh_dashboard_metrics", key=metric_key)
        assert cli(operations, "refresh_dashboard_metrics", key=metric_key) == metrics
    assert first == second and "PENDING" in first
    assert Job.objects.count() == 2 and ScoringRun.objects.count() == 0
    with pytest.raises(CommandError):
        cli(operations, "rebuild_scores", key=key, campaign=operations["campaign"].pk, limit=1)
    assert Job.objects.count() == 2


def test_rebuild_bounded_pagination_excludes_archived_and_inactive(operations):
    first_extra, second_extra = add_lead(operations), add_lead(operations)
    add_lead(operations, archived=True)
    add_lead(operations, active=False)
    eligible = {operations["lead"].pk, first_extra.pk, second_extra.pk}
    parent = enqueue(operations, limit=2)
    assert JobRunner().run_once(kinds=["REBUILD_SCORES"]).pk == parent.pk
    parent.refresh_from_db()
    assert parent.status == "SUCCEEDED" and parent.result["count"] == 2
    assert parent.progress_current == parent.progress_total == 2
    assert parent.result["next_after"]
    assert all(
        job.workspace_id == operations["member"].workspace_id
        for job in Job.objects.filter(kind="SCORE_LEAD")
    )
    next_parent = enqueue(operations, limit=2, after=parent.result["next_after"])
    assert JobRunner().run_once(kinds=["REBUILD_SCORES"]).pk == next_parent.pk
    next_parent.refresh_from_db()
    assert next_parent.result["count"] == 1 and next_parent.result["next_after"] is None
    children = list(Job.objects.filter(kind="SCORE_LEAD"))
    assert {uuid.UUID(job.payload["lead_id"]) for job in children} == eligible
    assert len(children) == 3
    assert not Job.objects.filter(workspace=operations["foreign"].workspace).exists()
    for _ in range(3):
        assert JobRunner().run_once(kinds=["SCORE_LEAD"]).status == "SUCCEEDED"
    assert ScoringRun.objects.count() == 3
    assert set(ScoringRun.objects.values_list("lead_id", flat=True)) == eligible


def test_rebuild_recovery_keeps_original_manifest_and_child_ids(operations):
    parent = enqueue(operations)
    assert JobRunner().run_once(kinds=["REBUILD_SCORES"]).status == "SUCCEEDED"
    parent.refresh_from_db()
    result = parent.result
    add_lead(operations)
    Job.objects.filter(pk=parent.pk).update(status="PENDING", result={})
    assert JobRunner().run_once(kinds=["REBUILD_SCORES"]).status == "SUCCEEDED"
    parent.refresh_from_db()
    assert parent.result == result and parent.attempts == 2
    assert Job.objects.filter(kind="SCORE_LEAD").count() == 1
    assert AuditEvent.objects.filter(action="SCORE_REBUILD_EXPANDED").count() == 1


def test_maximum_batch_manifest_fits_metadata_limit(operations):
    companies = Company.objects.bulk_create(
        [
            Company(
                workspace=operations["member"].workspace, name=f"Fictional volume company {index}"
            )
            for index in range(199)
        ]
    )
    Lead.objects.bulk_create(
        [
            Lead(
                workspace=operations["member"].workspace,
                campaign=operations["campaign"],
                company=company,
            )
            for company in companies
        ]
    )
    parent = enqueue(operations, limit=200)
    assert JobRunner().run_once(kinds=["REBUILD_SCORES"]).status == "SUCCEEDED"
    parent.refresh_from_db()
    assert parent.result["count"] == Job.objects.filter(kind="SCORE_LEAD").count() == 200
    assert len(json.dumps(parent.result).encode()) < 16000


def test_empty_rebuild_is_valid_and_cursor_must_be_scoped(operations):
    Lead.objects.filter(pk=operations["lead"].pk).update(is_archived=True)
    parent = enqueue(operations)
    JobRunner().run_once(kinds=["REBUILD_SCORES"])
    parent.refresh_from_db()
    assert parent.status == "SUCCEEDED" and parent.result["count"] == 0
    assert parent.result["job_ids"] == [] and parent.result["next_after"] is None
    for cursor in (str(operations["other_lead"].pk), str(uuid.uuid4())):
        with pytest.raises(ValidationError):
            enqueue(operations, after=cursor)


def test_child_enqueue_failure_rolls_back_batch(operations):
    add_lead(operations)
    parent = enqueue(operations)
    from apps.scoring.services import enqueue_score

    calls = 0

    def fail_second(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValidationError("Deliberate failure")
        return enqueue_score(**kwargs)

    with patch("apps.scoring.batches.enqueue_score", side_effect=fail_second):
        assert JobRunner().run_once(kinds=["REBUILD_SCORES"]).status == "FAILED"
    assert not Job.objects.filter(kind="SCORE_LEAD").exists()
    assert not AuditEvent.objects.filter(action="SCORE_REBUILD_EXPANDED").exists()
    parent.refresh_from_db()
    assert parent.progress_current == 0


def test_metrics_snapshot_is_scoped_durable_and_does_not_cache_live_dashboard(operations, client):
    parent = enqueue(operations, kind="REFRESH_METRICS")
    assert JobRunner().run_once(kinds=["REFRESH_METRICS"]).status == "SUCCEEDED"
    parent.refresh_from_db()
    result = parent.result
    assert result["workspace_id"] == str(operations["member"].workspace_id)
    assert result["companies_total"] == result["leads_total"] == result["lead_counts"]["NEW"] == 1
    assert result["jobs_failed"] == result["messages_pending"] == 0
    assert result["measured_at"].endswith("+00:00")
    add_lead(operations)
    Job.objects.filter(pk=parent.pk).update(status="PENDING", result={})
    JobRunner().run_once(kinds=["REFRESH_METRICS"])
    parent.refresh_from_db()
    assert parent.result == result
    assert AuditEvent.objects.filter(action="DASHBOARD_METRICS_REFRESHED").count() == 1
    snapshot = dashboard_for_user(
        user=operations["member"].user, workspace_id=operations["member"].workspace_id
    )
    assert snapshot.companies_total == 2
    new = enqueue(operations, kind="REFRESH_METRICS")
    JobRunner().run_once(kinds=["REFRESH_METRICS"])
    new.refresh_from_db()
    assert new.result["companies_total"] == 2
    client.force_login(operations["member"].user)
    response = client.get(reverse("jobs:detail", args=[operations["member"].workspace_id, new.pk]))
    assert response.status_code == 200 and b"measured_at" in response.content
    assert (
        client.get(
            reverse("jobs:detail", args=[operations["foreign"].workspace_id, new.pk])
        ).status_code
        == 404
    )


@pytest.mark.parametrize("role", Membership.Role.values)
def test_command_roles_follow_scoring_write_permissions(operations, role):
    Membership.objects.filter(pk=operations["member"].pk).update(role=role)
    if role == "VIEWER":
        for name, extra in (
            ("rebuild_scores", {"campaign": operations["campaign"].pk}),
            ("refresh_dashboard_metrics", {}),
        ):
            with pytest.raises(CommandError):
                cli(operations, name, **extra)
        assert Job.objects.count() == 0
    else:
        cli(operations, "rebuild_scores", campaign=operations["campaign"].pk)
        cli(operations, "refresh_dashboard_metrics")
        assert Job.objects.count() == 2


@pytest.mark.parametrize("kind", ["REBUILD_SCORES", "REFRESH_METRICS"])
def test_worker_rechecks_revoked_membership(operations, kind):
    parent = enqueue(operations, kind=kind)
    Membership.objects.filter(pk=operations["member"].pk).update(is_active=False)
    assert JobRunner().run_once(kinds=[kind]).status == "FAILED"
    assert not Job.objects.filter(kind="SCORE_LEAD").exists()
    assert not AuditEvent.objects.filter(
        action__in=["SCORE_REBUILD_EXPANDED", "DASHBOARD_METRICS_REFRESHED"]
    ).exists()
    parent.refresh_from_db()
    assert parent.result == {}


def test_invalid_actor_foreign_campaign_and_archival_fail_at_enqueue(operations):
    for actor in (
        uuid.uuid4(),
        UserFactory(is_superuser=True, is_staff=True).pk,
        UserFactory(is_active=False).pk,
    ):
        with pytest.raises(CommandError):
            call_command(
                "refresh_dashboard_metrics",
                workspace=operations["member"].workspace_id,
                actor=actor,
                idempotency_key=uuid.uuid4(),
            )
    with pytest.raises(CommandError):
        cli(operations, "rebuild_scores", campaign=operations["other_campaign"].pk)
    type(operations["campaign"]).objects.filter(pk=operations["campaign"].pk).update(
        status="ARCHIVED"
    )
    with pytest.raises(CommandError):
        cli(operations, "rebuild_scores", campaign=operations["campaign"].pk)
    assert Job.objects.count() == 0


def test_campaign_archived_after_enqueue_blocks_expansion(operations):
    parent = enqueue(operations)
    type(operations["campaign"]).objects.filter(pk=operations["campaign"].pk).update(
        status="ARCHIVED"
    )
    assert JobRunner().run_once(kinds=["REBUILD_SCORES"]).pk == parent.pk
    parent.refresh_from_db()
    assert parent.status == "FAILED" and not Job.objects.filter(kind="SCORE_LEAD").exists()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"campaign_id": "bad", "after": None, "limit": 1},
        {"campaign_id": str(uuid.uuid4()), "after": "bad", "limit": 1},
        {"campaign_id": str(uuid.uuid4()), "after": None, "limit": True},
        {"campaign_id": str(uuid.uuid4()), "after": None, "limit": 201},
        {"campaign_id": str(uuid.uuid4()), "after": None, "limit": 0},
    ],
)
def test_rebuild_payload_is_closed_and_bounded(payload):
    with pytest.raises(ValidationError):
        validate_payload("REBUILD_SCORES", payload)


@pytest.mark.parametrize("payload", [{"workspace_id": str(uuid.uuid4())}, [], None])
def test_metrics_payload_cannot_override_workspace(payload):
    with pytest.raises(ValidationError):
        validate_payload("REFRESH_METRICS", payload)


def test_context_fencing_rejects_stale_attempt_kind_payload_and_workspace(operations):
    parent = enqueue(operations, kind="REFRESH_METRICS")
    with WorkerLock() as lock:
        running = claim_next(lock=lock, kinds=["REFRESH_METRICS"])
        for context in (
            ExecutionContext(parent.pk, running.workspace_id, 0, {}, lambda *_: None),
            ExecutionContext(parent.pk, operations["foreign"].workspace_id, 1, {}, lambda *_: None),
            ExecutionContext(parent.pk, running.workspace_id, 1, {"forged": True}, lambda *_: None),
        ):
            with pytest.raises(StaleExecution):
                run_metrics(context)
        valid = ExecutionContext(parent.pk, running.workspace_id, 1, {}, lambda *_: None)
        with pytest.raises(StaleExecution):
            run_rebuild(valid)
    assert not AuditEvent.objects.filter(action="DASHBOARD_METRICS_REFRESHED").exists()
