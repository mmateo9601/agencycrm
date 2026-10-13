import io
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts import demo
from apps.accounts.models import AgencyProfile, Membership, ServiceOffering, User, Workspace
from apps.audit.models import AuditEvent
from apps.companies.models import Company, CompanyExternalRef, Contact
from apps.compliance.models import SuppressionEntry
from apps.crm.models import Lead, LeadStatusHistory, Opportunity
from apps.jobs.locking import WorkerAlreadyRunning, WorkerLock
from apps.jobs.models import Job, JobAttempt
from apps.jobs.services import JobService
from apps.outreach.models import MessageEvent, OutreachMessage, OutreachSettings
from apps.proposals.models import Proposal
from apps.scoring.models import ScoringRun
from tests.factories import MembershipFactory, UserFactory, WorkspaceFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def demo_files(settings, tmp_path, monkeypatch):
    settings.MEDIA_ROOT = tmp_path / "media"
    monkeypatch.setattr("apps.jobs.locking.worker_lock_path", lambda: tmp_path / "worker.lock")
    return tmp_path


@pytest.fixture
def seeded(demo_files):
    return demo.seed_demo()


def test_command_creates_complete_fictional_demo_without_providers(demo_files, settings):
    # Even configured live adapters are irrelevant: the seed selects no provider.
    settings.EMAIL_PROVIDER = "smtp"
    settings.WHATSAPP_PROVIDER = "cloud"
    settings.PLACES_PROVIDER = "google"
    output = io.StringIO()
    with (
        patch("apps.outreach.services.outbound_provider", side_effect=AssertionError("send")),
        patch("integrations.common.factory.places_provider", side_effect=AssertionError("places")),
        patch("apps.jobs.runner.JobRunner.run_once", side_effect=AssertionError("global runner")),
    ):
        call_command("seed_demo", stdout=output)
    workspace = Workspace.objects.get(slug="demo-agency")
    assert workspace.pk == demo.DEMO_WORKSPACE_ID
    assert str(workspace.pk) in output.getvalue()
    actor = User.objects.get(pk=demo.DEMO_ACTOR_ID)
    assert not actor.has_usable_password() and not actor.is_staff and not actor.is_superuser
    assert Membership.objects.get(user=actor, workspace=workspace).role == "OWNER"
    assert AgencyProfile.objects.get(workspace=workspace).trading_name == "Demo Agency"
    assert ServiceOffering.objects.filter(workspace=workspace).count() == 2
    assert Company.objects.count() == Contact.objects.count() == Lead.objects.count() == 10
    assert CompanyExternalRef.objects.filter(provider="SEED_DEMO").count() == 10
    assert Opportunity.objects.count() >= 10 and ScoringRun.objects.count() == 10
    assert set(Lead.objects.values_list("status", flat=True)) == {
        "NEW",
        "ENRICHING",
        "QUALIFIED",
        "READY",
        "CONTACTED",
        "REPLIED",
        "MEETING",
        "PROPOSAL",
        "WON",
        "LOST",
    }
    assert LeadStatusHistory.objects.count() > 10
    assert all(
        value.endswith(".invalid") for value in Contact.objects.values_list("email", flat=True)
    )
    assert not Contact.objects.exclude(phone="").exists()
    assert not Contact.objects.exclude(whatsapp_e164="").exists()
    proposal = Proposal.objects.get()
    assert proposal.status == "GENERATED" and proposal.approved_at is None
    with proposal.pdf.open("rb") as document:
        assert document.read(5) == b"%PDF-"
    assert OutreachMessage.objects.count() == 2
    assert set(OutreachMessage.objects.values_list("provider", flat=True)) == {"mock"}
    assert set(OutreachMessage.objects.values_list("status", flat=True)) == {"PENDING_APPROVAL"}
    assert not OutreachMessage.objects.exclude(approved_at=None).exists()
    assert not MessageEvent.objects.filter(event_type__in=["SENT", "SEND_ATTEMPT"]).exists()
    assert SuppressionEntry.objects.count() == 1
    assert Job.objects.count() == 2 and not Job.objects.exclude(status="CANCELLED").exists()
    assert JobAttempt.objects.count() == 0
    config = OutreachSettings.objects.get(workspace=workspace)
    assert not config.live_mode_enabled and config.manual_approval_required


def test_rerun_keeps_ids_counts_files_and_operator_edits(seeded, demo_files):
    company = Company.objects.first()
    company.name = "Nombre editado por el operador"
    company.save()
    before = {
        model: list(model.objects.values_list("pk", flat=True))
        for model in (
            User,
            Workspace,
            Company,
            Contact,
            Lead,
            Proposal,
            OutreachMessage,
            Job,
            AuditEvent,
            ScoringRun,
            Opportunity,
            Membership,
        )
    }
    files = list((demo_files / "media").rglob("*.pdf"))
    result = demo.seed_demo()
    assert not result.created and result.workspace_id == seeded.workspace_id
    company.refresh_from_db()
    assert company.name == "Nombre editado por el operador"
    assert before == {model: list(model.objects.values_list("pk", flat=True)) for model in before}
    assert list((demo_files / "media").rglob("*.pdf")) == files


def test_optional_account_is_explicit_unusable_and_never_reset(seeded):
    result = demo.seed_demo(username="demo-operator")
    assert result.user_created and not result.created
    user = User.objects.get(username="demo-operator")
    assert not user.has_usable_password() and not user.is_superuser and not user.is_staff
    membership = Membership.objects.get(user=user)
    assert membership.workspace_id == seeded.workspace_id and membership.role == "OWNER"
    user.set_password("Changed-by-operator-42!")
    user.save()
    membership.role = "VIEWER"
    membership.is_active = False
    membership.save()
    assert not demo.seed_demo(username=user.username).user_created
    user.refresh_from_db()
    membership.refresh_from_db()
    assert user.check_password("Changed-by-operator-42!")
    assert membership.role == "VIEWER" and not membership.is_active


def test_command_optional_account_and_invalid_name(seeded):
    output = io.StringIO()
    call_command("seed_demo", create_user="demo-cli", stdout=output)
    assert "Usuario creado sin contraseña utilizable" in output.getvalue()
    assert not User.objects.get(username="demo-cli").has_usable_password()
    with pytest.raises(CommandError):
        call_command("seed_demo", create_user="bad name", stdout=output)


@pytest.mark.parametrize("collision", ["slug", "workspace_id", "actor_name", "actor_id", "user"])
def test_reserved_identity_and_existing_user_collisions_fail_without_changes(demo_files, collision):
    username = None
    if collision == "slug":
        WorkspaceFactory(slug=demo.DEMO_SLUG)
    elif collision == "workspace_id":
        WorkspaceFactory(id=demo.DEMO_WORKSPACE_ID)
    elif collision == "actor_name":
        UserFactory(username=demo.DEMO_ACTOR_NAME)
    elif collision == "actor_id":
        UserFactory(id=demo.DEMO_ACTOR_ID)
    else:
        username = "existing-operator"
        UserFactory(username=username)
    before = (User.objects.count(), Workspace.objects.count())
    with pytest.raises(ValidationError):
        demo.seed_demo(username=username)
    assert before == (User.objects.count(), Workspace.objects.count())
    assert Company.objects.count() == Proposal.objects.count() == Membership.objects.count() == 0


def test_failure_rolls_back_database_and_only_new_pdf(demo_files):
    existing = demo_files / "media" / "unrelated.pdf"
    existing.parent.mkdir()
    existing.write_bytes(b"existing file must remain")
    original = demo.create_demo_data

    def fail_after_pdf(**kwargs):
        original(**kwargs)
        raise RuntimeError("Deliberate seed failure")

    with patch("apps.accounts.demo.create_demo_data", side_effect=fail_after_pdf):
        with pytest.raises(RuntimeError, match="Deliberate"):
            demo.seed_demo(username="demo-operator")
    assert User.objects.count() == Workspace.objects.count() == Company.objects.count() == 0
    assert AuditEvent.objects.count() == Proposal.objects.count() == 0
    assert list(existing.parent.rglob("*.pdf")) == [existing]
    assert demo.seed_demo().created


def test_seed_does_not_claim_or_modify_other_workspace_jobs(demo_files):
    member = MembershipFactory(role="OWNER")
    job = JobService.enqueue(
        user=member.user,
        workspace_id=member.workspace_id,
        kind="CHECK_MOCK_PROVIDERS",
        payload={"scenario": "success"},
        idempotency_key="unrelated-job",
    )
    before = list(
        AuditEvent.objects.filter(workspace=member.workspace).values_list("pk", flat=True)
    )
    demo.seed_demo()
    job.refresh_from_db()
    assert job.status == "PENDING" and job.attempts == 0
    assert before == list(
        AuditEvent.objects.filter(workspace=member.workspace).values_list("pk", flat=True)
    )
    assert not Company.objects.filter(workspace=member.workspace).exists()


@pytest.mark.parametrize("change", ["workspace", "actor", "credential", "membership"])
def test_seed_never_repairs_revoked_access(seeded, change):
    if change == "workspace":
        Workspace.objects.filter(pk=seeded.workspace_id).update(is_active=False)
    elif change == "actor":
        User.objects.filter(pk=demo.DEMO_ACTOR_ID).update(is_active=False)
    elif change == "credential":
        actor = User.objects.get(pk=demo.DEMO_ACTOR_ID)
        actor.set_password("Forbidden-technical-login-42!")
        actor.save()
    else:
        Membership.objects.filter(user_id=demo.DEMO_ACTOR_ID).update(role="VIEWER")
    before = AuditEvent.objects.count()
    with pytest.raises(ValidationError):
        demo.seed_demo()
    assert before == AuditEvent.objects.count()


def test_live_mode_and_worker_lock_fail_cleanly(demo_files, settings):
    settings.OUTREACH_MODE = "live"
    with pytest.raises(CommandError, match="simulation"):
        call_command("seed_demo")
    assert Workspace.objects.count() == 0
    settings.OUTREACH_MODE = "simulation"
    with WorkerLock():
        with pytest.raises(WorkerAlreadyRunning):
            demo.seed_demo()
    assert Workspace.objects.count() == 0


@pytest.mark.parametrize("username", ["", " leading", "bad name", "x" * 151])
def test_invalid_optional_username_has_no_side_effects(demo_files, username):
    with pytest.raises(ValidationError):
        demo.seed_demo(username=username)
    assert User.objects.count() == Workspace.objects.count() == 0
