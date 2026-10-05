import io
import subprocess
import sys
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.http import Http404
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.jobs.execution import StaleExecution, claim_next, finish_success, update_progress
from apps.jobs.handlers import HANDLERS, check_mock_providers
from apps.jobs.locking import WorkerAlreadyRunning, WorkerLock
from apps.jobs.models import Job, JobAttempt
from apps.jobs.recovery import requeue_stale
from apps.jobs.runner import JobRunner
from apps.jobs.services import JobService
from integrations.common.errors import ProviderRateLimitError, ProviderTemporaryError
from tests.factories import MembershipFactory, UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def isolated_worker_lock(tmp_path, monkeypatch):
    path = tmp_path / "test.worker.lock"
    monkeypatch.setattr("apps.jobs.locking.worker_lock_path", lambda: path)
    return path


@pytest.fixture
def member():
    return MembershipFactory(role="OWNER")


def enqueue(member, **kwargs):
    params = {
        "user": member.user,
        "workspace_id": member.workspace_id,
        "kind": Job.Kind.CHECK_MOCK_PROVIDERS,
        "payload": {"scenario": "success"},
    }
    return JobService.enqueue(**(params | kwargs))


def url(member, route_name, job=None, **kwargs):
    params = {"workspace_id": member.workspace_id}
    if job is not None:
        params["pk"] = job.pk
    params.update(kwargs)
    return reverse(f"jobs:{route_name}", kwargs=params)


def make_stale(member, *, max_attempts=3):
    job = enqueue(member, max_attempts=max_attempts)
    with WorkerLock() as lock:
        job = claim_next(lock=lock)
    Job.objects.filter(pk=job.pk).update(started_at=timezone.now() - timedelta(hours=1))
    job.refresh_from_db()
    return job


def test_enqueue_is_idempotent_with_workspace_scope(member):
    key = "same-request"
    job = enqueue(member, idempotency_key=key)
    assert enqueue(member, idempotency_key=key).pk == job.pk
    assert AuditEvent.objects.filter(action="JOB_ENQUEUED").count() == 1
    for change in ({"payload": {"scenario": "permanent"}}, {"priority": 10}, {"max_attempts": 1}):
        with pytest.raises(ValidationError):
            enqueue(member, idempotency_key=key, **change)
    foreign = MembershipFactory()
    assert enqueue(foreign, idempotency_key=key).pk != job.pk
    assert enqueue(member).pk != enqueue(member).pk
    with pytest.raises(IntegrityError), transaction.atomic():
        Job.objects.create(workspace=member.workspace, kind=job.kind, idempotency_key=key)


def test_duplicate_scheduled_request_after_retry_preserves_job(member):
    scheduled = timezone.now() - timedelta(minutes=1)
    params = {
        "idempotency_key": "scheduled-request",
        "available_at": scheduled,
        "payload": {"scenario": "temporary_once"},
    }
    job = enqueue(member, **params)
    retried = JobRunner().run_once()
    assert retried.available_at > scheduled
    duplicate = enqueue(member, **params)
    assert duplicate.pk == job.pk
    assert duplicate.available_at == retried.available_at
    assert duplicate.attempts == 1
    assert AuditEvent.objects.filter(action="JOB_ENQUEUED").count() == 1


def test_job_state_attempt_and_audit_are_atomic(member):
    job = enqueue(member)
    with WorkerLock() as lock:
        with (
            patch("apps.jobs.execution.record_event", side_effect=RuntimeError("audit failed")),
            pytest.raises(RuntimeError),
        ):
            claim_next(lock=lock)
        job.refresh_from_db()
        assert job.status == "PENDING" and job.attempts == 0
        assert not job.attempt_history.exists()
        job = claim_next(lock=lock)
        with (
            patch("apps.jobs.execution.record_event", side_effect=RuntimeError("audit failed")),
            pytest.raises(RuntimeError),
        ):
            finish_success(lock=lock, job=job, result={"ok": True})
        job.refresh_from_db()
        assert job.status == "RUNNING" and job.result == {}
        assert job.attempt_history.get().finished_at is None
        assert not AuditEvent.objects.filter(action="JOB_SUCCEEDED").exists()


def test_recovery_is_scoped_and_rolls_back_when_audit_fails(member):
    own = make_stale(member)
    foreign = make_stale(MembershipFactory(role="OWNER"))
    with (
        patch("apps.jobs.recovery.record_event", side_effect=RuntimeError("audit failed")),
        pytest.raises(RuntimeError),
    ):
        JobService.recover(user=member.user, workspace_id=member.workspace_id, pk=own.pk)
    own.refresh_from_db()
    assert own.status == "RUNNING" and own.attempt_history.get().finished_at is None
    assert JobService.recover(user=member.user, workspace_id=member.workspace_id, pk=own.pk) == {
        "requeued": 1,
        "failed": 0,
    }
    foreign.refresh_from_db()
    assert foreign.status == "RUNNING" and foreign.attempt_history.get().finished_at is None


def test_worker_rejects_payloads_inserted_outside_service(member):
    job = enqueue(member)
    Job.objects.filter(pk=job.pk).update(payload={"company_id": str(uuid.uuid4())})
    with patch.dict(HANDLERS, {job.kind: lambda _: pytest.fail("Unvalidated payload executed")}):
        assert JobRunner().run_once().status == "FAILED"
    assert job.attempt_history.get().error_type == "ValidationError"


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "SEND_OUTREACH"},
        {"kind": "RUN_SEARCH_ITEM"},
        {"kind": "unknown"},
        {"payload": {"company_id": "foreign-uuid"}},
        {"payload": {"scenario": "live"}},
        {"payload": {"scenario": []}},
        {"payload": []},
        {"max_attempts": 0},
        {"max_attempts": 11},
        {"priority": 101},
        {"idempotency_key": "x" * 101},
        {"idempotency_key": 10},
        {"available_at": timezone.datetime(2026, 1, 1)},
    ],
)
def test_invalid_job_inputs_are_rejected(member, change):
    with pytest.raises(ValidationError):
        enqueue(member, **change)
    assert Job.objects.count() == 0


def test_worker_success_and_deterministic_mock_results(member):
    job = enqueue(member, idempotency_key="diagnostic")
    assert job.status == "PENDING" and job.attempt_history.count() == 0
    result = JobRunner().run_once()
    assert result.pk == job.pk and result.status == "SUCCEEDED"
    assert result.progress_current == result.progress_total == 3
    assert result.result["simulated"] is True and result.result["places_count"] == 3
    attempt = result.attempt_history.get()
    assert attempt.succeeded and attempt.finished_at is not None and attempt.error_type == ""
    assert result.run_token is None
    assert JobRunner().run_once() is None
    assert enqueue(member, idempotency_key="diagnostic").pk == job.pk
    assert list(
        AuditEvent.objects.filter(object_id=job.pk)
        .order_by("created_at")
        .values_list("action", flat=True)
    ) == ["JOB_ENQUEUED", "JOB_STARTED", "JOB_SUCCEEDED"]


def test_priority_availability_kind_filters_and_inactive_workspace(member):
    first = enqueue(member, priority=1)
    higher = enqueue(member, priority=10)
    future = enqueue(member, priority=100, available_at=timezone.now() + timedelta(days=1))
    assert JobRunner().run_once(kinds=["ENRICH_COMPANY"]) is None
    assert JobRunner().run_once().pk == higher.pk
    assert JobRunner().run_once().pk == first.pk
    assert JobRunner().run_once() is None
    future.refresh_from_db()
    assert future.attempts == 0
    enqueue(member)
    member.workspace.is_active = False
    member.workspace.save()
    assert JobRunner().run_once() is None


def test_transient_failure_retries_once_with_backoff(member):
    job = enqueue(member, payload={"scenario": "temporary_once"})
    before = timezone.now()
    result = JobRunner().run_once()
    assert result.status == "PENDING" and result.attempts == 1
    assert result.available_at >= before + timedelta(seconds=5)
    assert result.started_at is None and result.finished_at is None
    assert result.attempt_history.get().error_type == "ProviderTemporaryError"
    assert JobRunner().run_once() is None
    Job.objects.filter(pk=job.pk).update(available_at=timezone.now() - timedelta(seconds=1))
    result = JobRunner().run_once()
    assert result.status == "SUCCEEDED" and result.attempts == 2
    assert list(
        result.attempt_history.order_by("attempt_number").values_list("succeeded", flat=True)
    ) == [False, True]


def test_retry_budget_and_permanent_failure(member, monkeypatch):
    job = enqueue(member, max_attempts=2)

    def fail(context):
        raise ProviderTemporaryError("secret raw payload")

    monkeypatch.setitem(HANDLERS, job.kind, fail)
    assert JobRunner().run_once().status == "PENDING"
    Job.objects.filter(pk=job.pk).update(available_at=timezone.now())
    failed = JobRunner().run_once()
    assert failed.status == "FAILED" and failed.attempts == 2
    assert failed.finished_at is not None and JobRunner().run_once() is None
    monkeypatch.setitem(HANDLERS, job.kind, check_mock_providers)
    job = enqueue(member, payload={"scenario": "permanent"}, max_attempts=10)
    assert JobRunner().run_once().status == "FAILED"
    assert job.attempt_history.count() == 1


def test_errors_are_sanitized_and_worker_continues(member, monkeypatch, caplog):
    job = enqueue(member)

    def fail(context):
        raise RuntimeError("password=very-secret-token email=private@example.com")

    monkeypatch.setitem(HANDLERS, job.kind, fail)
    failed = JobRunner().run_once()
    attempt = failed.attempt_history.get()
    assert failed.status == "FAILED" and attempt.error_type == "RuntimeError"
    assert "test_jobs.py" in attempt.traceback_excerpt
    serialized = (
        f"{failed.last_error} {attempt.error_message} {attempt.traceback_excerpt} {caplog.text}"
    )
    assert "very-secret-token" not in serialized and "private@example.com" not in serialized
    monkeypatch.setitem(HANDLERS, job.kind, check_mock_providers)
    next_job = enqueue(member)
    assert JobRunner().run_once().pk == next_job.pk


def test_rate_limit_retry_after_is_respected(member, monkeypatch):
    job = enqueue(member)

    def rate_limit(context):
        raise ProviderRateLimitError(retry_after=45)

    monkeypatch.setitem(HANDLERS, job.kind, rate_limit)
    before = timezone.now()
    result = JobRunner().run_once()
    assert result.status == "PENDING" and result.available_at >= before + timedelta(seconds=45)


@pytest.mark.django_db(transaction=True)
def test_handler_runs_outside_transactions_and_progress_is_visible(member, monkeypatch):
    job = enqueue(member)
    seen = []

    def observe(context):
        assert not connection.in_atomic_block
        context.progress(1, 2)
        client = Client()
        client.force_login(member.user)
        response = client.get(url(member, "progress", job))
        seen.append(response.json()["current"])
        context.progress(2, 2)
        return {"ok": True}

    monkeypatch.setitem(HANDLERS, job.kind, observe)
    assert JobRunner().run_once().status == "SUCCEEDED"
    assert seen == [1]


def test_revoked_membership_prevents_execution(member):
    job = enqueue(member)
    member.is_active = False
    member.save()
    with patch(
        "apps.jobs.runner.HANDLERS", {job.kind: lambda context: pytest.fail("Should not execute")}
    ):
        result = JobRunner().run_once()
    assert result.status == "FAILED" and result.attempt_history.get().error_type == "Http404"


def test_recovery_requires_stopped_worker_and_preserves_attempt_budget(member):
    job = make_stale(member)
    with WorkerLock(), pytest.raises(WorkerAlreadyRunning):
        JobService.recover(user=member.user, workspace_id=member.workspace_id, pk=job.pk)
    result = JobService.recover(user=member.user, workspace_id=member.workspace_id, pk=job.pk)
    assert result == {"requeued": 1, "failed": 0}
    job.refresh_from_db()
    assert job.status == "PENDING" and job.attempts == 1 and job.run_token is None
    assert job.attempt_history.get().error_type == "WorkerInterrupted"
    assert JobRunner().run_once().attempts == 2
    exhausted = make_stale(member, max_attempts=1)
    assert requeue_stale() == {"requeued": 0, "failed": 1}
    exhausted.refresh_from_db()
    assert exhausted.status == "FAILED" and exhausted.attempts == 1


def test_fresh_running_job_is_not_recovered_and_old_token_cannot_write(member):
    job = enqueue(member)
    with WorkerLock() as lock:
        claimed = claim_next(lock=lock)
        assert claimed.pk == job.pk
    assert requeue_stale() == {"requeued": 0, "failed": 0}
    Job.objects.filter(pk=job.pk).update(started_at=timezone.now() - timedelta(hours=1))
    requeue_stale()
    with pytest.raises(StaleExecution):
        update_progress(job=claimed, current=1, total=1)
    with WorkerLock() as lock, pytest.raises(StaleExecution):
        finish_success(lock=lock, job=claimed, result={"stale": True})
    job.refresh_from_db()
    assert job.status == "PENDING" and job.result == {}


def test_cancel_is_idempotent_and_never_cancels_running_job(member):
    job = enqueue(member)
    args = {"user": member.user, "workspace_id": member.workspace_id, "pk": job.pk}
    assert JobService.cancel(**args).status == "CANCELLED"
    assert JobService.cancel(**args).status == "CANCELLED"
    assert JobRunner().run_once() is None
    running = make_stale(member)
    with pytest.raises(ValidationError):
        JobService.cancel(**(args | {"pk": running.pk}))
    assert AuditEvent.objects.filter(action="JOB_CANCELLED").count() == 1


def test_worker_lock_is_exclusive_across_processes_and_releases_after_exit(isolated_worker_lock):
    code = """
import sys
from pathlib import Path
from apps.jobs.locking import WorkerLock, WorkerAlreadyRunning
try:
    with WorkerLock(Path(sys.argv[1])):
        print('acquired')
except WorkerAlreadyRunning:
    sys.exit(2)
"""
    command = [sys.executable, "-c", code, str(isolated_worker_lock)]
    with WorkerLock():
        process = subprocess.run(command, capture_output=True, text=True, timeout=15)
        assert process.returncode == 2, process.stderr
    process = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert process.returncode == 0 and "acquired" in process.stdout, process.stderr
    with WorkerLock():
        pass


def test_worker_commands_once_filters_and_recovery(member):
    output = io.StringIO()
    job = enqueue(member)
    call_command("run_worker", once=True, kinds="ENRICH_COMPANY,SCORE_LEAD", stdout=output)
    job.refresh_from_db()
    assert job.status == "PENDING"
    call_command("run_worker", once=True, kinds="CHECK_MOCK_PROVIDERS", stdout=output)
    job.refresh_from_db()
    assert job.status == "SUCCEEDED"
    call_command("run_worker", once=True, stdout=output)
    assert "No hay trabajos" in output.getvalue()
    for kwargs in ({"sleep": 0}, {"sleep": float("nan")}, {"kinds": "BOGUS"}):
        with pytest.raises(CommandError):
            call_command("run_worker", once=True, stdout=output, **kwargs)
    with WorkerLock(), pytest.raises(CommandError):
        call_command("run_worker", once=True, stdout=output)
    stale = make_stale(member)
    call_command("requeue_stale_jobs", workspace=member.workspace_id, stdout=output)
    stale.refresh_from_db()
    assert stale.status == "PENDING"
    with pytest.raises(CommandError):
        call_command("requeue_stale_jobs", older_than=0)


def test_job_constraints_and_progress_validation(member):
    job = enqueue(member)
    for fields in (
        {"status": "BOGUS"},
        {"progress_current": 2, "progress_total": 1},
        {"attempts": 5},
        {"status": "RUNNING"},
        {"priority": 101},
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            Job.objects.filter(pk=job.pk).update(**fields)
    with WorkerLock() as lock:
        job = claim_next(lock=lock)
        with pytest.raises(ValidationError):
            update_progress(job=job, current=3, total=2)
        update_progress(job=job, current=2, total=3)
        with pytest.raises(StaleExecution):
            update_progress(job=job, current=1, total=3)
        with pytest.raises(IntegrityError), transaction.atomic():
            JobAttempt.objects.create(job=job, attempt_number=1)
    with pytest.raises(RuntimeError):
        claim_next(lock=lock)


def test_job_ui_enqueues_without_execution_and_has_csrf(member):
    client = Client(enforce_csrf_checks=True)
    client.force_login(member.user)
    response = client.get(url(member, "create"))
    assert response.status_code == 200
    data = {
        "scenario": "success",
        "priority": 0,
        "max_attempts": 3,
        "idempotency_key": str(uuid.uuid4()),
    }
    assert client.post(url(member, "create"), data).status_code == 403
    data["csrfmiddlewaretoken"] = client.cookies["csrftoken"].value
    with patch(
        "apps.jobs.runner.JobRunner.run_once",
        side_effect=AssertionError("Views must not execute jobs"),
    ):
        assert client.post(url(member, "create"), data).status_code == 302
        assert client.post(url(member, "create"), data).status_code == 302
    job = Job.objects.get()
    assert job.status == "PENDING" and job.attempts == 0
    assert client.get(url(member, "list")).status_code == 200
    assert client.get(url(member, "detail", job)).status_code == 200
    assert b"jobs/progress.js" in client.get(url(member, "detail", job)).content
    assert client.get(url(member, "progress", job)).json()["status"] == "PENDING"
    assert client.get(url(member, "action", job, action="cancel")).status_code == 405
    assert client.post(url(member, "action", job, action="cancel")).status_code == 403
    assert (
        client.post(
            url(member, "action", job, action="cancel"),
            {"csrfmiddlewaretoken": data["csrfmiddlewaretoken"]},
        ).status_code
        == 302
    )


def test_job_cross_workspace_ids_filters_and_roles(client, member):
    own = enqueue(member)
    other = MembershipFactory(role="OWNER")
    foreign = enqueue(other)
    MembershipFactory(user=member.user, workspace=other.workspace)
    client.force_login(member.user)
    response = client.get(url(member, "list"))
    assert list(response.context["page_obj"]) == [own]
    assert str(foreign.pk).encode() not in response.content
    for action in ("detail", "progress"):
        assert client.get(url(member, action, foreign)).status_code == 404
    for action in ("cancel", "recover"):
        assert client.post(url(member, "action", foreign, action=action)).status_code == 404
    with pytest.raises(Http404):
        JobService.recover(user=member.user, workspace_id=member.workspace_id, pk=foreign.pk)
    assert (
        client.get(url(member, "list"), {"status": "FAILED"}).context["page_obj"].paginator.count
        == 0
    )
    assert client.get(url(member, "list"), {"status": "INVALID"}).context["filter_form"].errors
    member.role = "VIEWER"
    member.save()
    assert client.get(url(member, "list")).status_code == 200
    assert client.get(url(member, "create")).status_code == 403
    assert client.post(url(member, "action", own, action="cancel")).status_code == 403
    with pytest.raises(PermissionDenied):
        enqueue(member)
    member.is_active = False
    member.save()
    assert client.get(url(member, "progress", own)).status_code == 404


def test_recovery_ui_and_admin_are_safe(client, member):
    stale = make_stale(member)
    client.force_login(member.user)
    assert b"Recuperar trabajo" in client.get(url(member, "detail", stale)).content
    with WorkerLock():
        response = client.post(url(member, "action", stale, action="recover"), follow=True)
    assert "Ya hay un worker" in response.content.decode()
    stale.refresh_from_db()
    assert stale.status == "RUNNING"
    assert client.post(url(member, "action", stale, action="recover")).status_code == 302
    stale.refresh_from_db()
    assert stale.status == "PENDING"
    superuser = UserFactory(is_staff=True, is_superuser=True)
    client.force_login(superuser)
    assert client.get(url(member, "list")).status_code == 404
    assert client.get(reverse("admin:jobs_job_change", args=[stale.pk])).status_code == 200
    assert (
        client.post(
            reverse("admin:jobs_job_change", args=[stale.pk]), {"status": "SUCCEEDED"}
        ).status_code
        == 403
    )
    assert (
        client.post(reverse("admin:jobs_job_delete", args=[stale.pk]), {"post": "yes"}).status_code
        == 403
    )
