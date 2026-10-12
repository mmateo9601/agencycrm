import smtplib
import ssl
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.compliance.policies import eligibility
from apps.compliance.services import add_suppression
from apps.jobs.models import Job
from apps.jobs.runner import JobRunner
from apps.outreach.email_recovery import recover_email
from apps.outreach.email_services import configure_connection, set_live_mode
from apps.outreach.models import EmailConnection, OutreachMessage, OutreachSettings
from apps.outreach.services import pause
from integrations.common.contracts import SendAttachment, SendMessageRequest
from integrations.common.errors import (
    EmailConfigurationError,
    ProviderAuthError,
    ProviderDeliveryUnknownError,
    ProviderPermanentError,
    ProviderTemporaryError,
)
from integrations.common.factory import outbound_provider
from integrations.email.smtp import SMTPEmailProvider, configuration_ready, message_id
from tests.factories import MembershipFactory
from tests.test_outreach import approve, prepare, queue
from tests.test_outreach import domain as domain
from tests.test_outreach import outreach as outreach
from tests.test_outreach import proposals as proposals

pytestmark = pytest.mark.django_db


@pytest.fixture
def smtp(settings):
    settings.OUTREACH_SMTP_HOST = "smtp.example.invalid"
    settings.OUTREACH_SMTP_PORT = 587
    settings.OUTREACH_SMTP_USERNAME = "fixture@example.invalid"
    settings.OUTREACH_SMTP_PASSWORD = "fictitious-secret-never-log"
    settings.OUTREACH_SMTP_SENDER = "agency@example.invalid"
    settings.OUTREACH_SMTP_SECURITY = "starttls"
    settings.OUTREACH_SMTP_TIMEOUT = 10
    settings.OUTREACH_MODE = "live"
    settings.EMAIL_PROVIDER = "smtp"
    with patch("integrations.email.smtp.smtplib.SMTP") as factory:
        client = factory.return_value
        client.send_message.return_value = {}
        yield factory, client


def connect(env, *, enabled=True, revision=0, name="Agencia ficticia"):
    member = env["member"]
    return configure_connection(
        user=member.user,
        workspace_id=member.workspace_id,
        sender_name=name,
        enabled=enabled,
        expected_revision=revision,
    )


def live(env, enabled=True):
    member = env["member"]
    config = OutreachSettings.objects.get(workspace_id=member.workspace_id)
    set_live_mode(
        user=member.user,
        workspace_id=member.workspace_id,
        enabled=enabled,
        expected_revision=config.revision,
    )


def recover(env, message, action):
    member = env["member"]
    return recover_email(
        user=member.user,
        workspace_id=member.workspace_id,
        pk=message.pk,
        action=action,
        evidence="Verificación del servidor ficticio 123",
        confirmed=True,
    )


def request():
    return SendMessageRequest(
        uuid.uuid4(),
        "intent",
        "recipient@example.invalid",
        "Contenido ficticio",
        subject="Propuesta",
        sender_email="agency@example.invalid",
        sender_name="Agencia ficticia",
        attachments=(SendAttachment("proposal.pdf", "application/pdf", b"%PDF-fixture"),),
    )


def test_tls_mime_and_stable_message_id(smtp, settings):
    factory, client = smtp
    result = SMTPEmailProvider().send(request())
    assert not result.simulated
    assert result.provider_message_id.startswith("<")
    assert len(result.provider_message_id) <= 100
    factory.assert_called_once_with("smtp.example.invalid", 587, timeout=10)
    client.starttls.assert_called_once()
    assert client.starttls.call_args.kwargs["context"].verify_mode == ssl.CERT_REQUIRED
    client.login.assert_called_once_with(
        settings.OUTREACH_SMTP_USERNAME, settings.OUTREACH_SMTP_PASSWORD
    )
    mail = client.send_message.call_args.args[0]
    assert mail["Message-ID"] == result.provider_message_id
    assert mail["From"].addresses[0].addr_spec == settings.OUTREACH_SMTP_SENDER
    assert mail.get_body(preferencelist=("plain",)).get_content().strip() == "Contenido ficticio"
    assert list(mail.iter_attachments())[0].get_payload(decode=True) == b"%PDF-fixture"
    client.close.assert_called_once()
    req = request()
    assert message_id(req) == message_id(req)


def test_implicit_tls_uses_verified_ssl(smtp, settings):
    settings.OUTREACH_SMTP_SECURITY = "ssl"
    with patch("integrations.email.smtp.smtplib.SMTP_SSL") as factory:
        factory.return_value.send_message.return_value = {}
        assert not SMTPEmailProvider().send(request()).simulated
        assert factory.call_args.kwargs["context"].verify_mode == ssl.CERT_REQUIRED
        factory.return_value.starttls.assert_not_called()
    smtp[0].assert_not_called()


@pytest.mark.parametrize(
    ("method", "error", "expected"),
    [
        ("login", smtplib.SMTPAuthenticationError(535, b"secret"), ProviderAuthError),
        ("starttls", smtplib.SMTPNotSupportedError("secret"), ProviderPermanentError),
        ("login", TimeoutError("secret"), ProviderTemporaryError),
        ("send_message", smtplib.SMTPDataError(451, b"secret"), ProviderTemporaryError),
        ("send_message", smtplib.SMTPDataError(550, b"secret"), ProviderPermanentError),
        (
            "send_message",
            smtplib.SMTPRecipientsRefused({"recipient@example.invalid": (450, b"secret")}),
            ProviderTemporaryError,
        ),
        (
            "send_message",
            smtplib.SMTPRecipientsRefused({"recipient@example.invalid": (550, b"secret")}),
            ProviderPermanentError,
        ),
        ("send_message", TimeoutError("secret"), ProviderDeliveryUnknownError),
        ("send_message", smtplib.SMTPServerDisconnected("secret"), ProviderDeliveryUnknownError),
        ("send_message", ssl.SSLError("secret"), ProviderDeliveryUnknownError),
    ],
)
def test_normalized_smtp_errors(smtp, method, error, expected):
    getattr(smtp[1], method).side_effect = error
    with pytest.raises(expected) as caught:
        SMTPEmailProvider().send(request())
    assert "secret" not in str(caught.value)
    smtp[1].close.assert_called_once()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("OUTREACH_SMTP_PASSWORD", ""),
        ("OUTREACH_SMTP_SECURITY", "none"),
        ("OUTREACH_SMTP_PORT", 0),
        ("OUTREACH_SMTP_TIMEOUT", 0),
        ("OUTREACH_SMTP_SENDER", "invalid"),
        ("OUTREACH_SMTP_HOST", "https://smtp.invalid"),
    ],
)
def test_incomplete_or_insecure_configuration_has_no_transport(smtp, settings, field, value):
    setattr(settings, field, value)
    assert not configuration_ready()
    with pytest.raises(EmailConfigurationError):
        SMTPEmailProvider().send(request())
    smtp[0].assert_not_called()


def test_header_injection_rejected_before_transport(smtp):
    req = SendMessageRequest(
        uuid.uuid4(),
        "key",
        "recipient@example.invalid",
        "body",
        subject="Subject\r\nBcc: victim@example.invalid",
        sender_email="agency@example.invalid",
    )
    with pytest.raises(ProviderPermanentError):
        SMTPEmailProvider().send(req)
    smtp[0].assert_not_called()


def test_live_send_retains_receipt_and_never_repeats(outreach, smtp):
    connect(outreach)
    live(outreach)
    message = approve(outreach, prepare(outreach, real_email=True))
    job = queue(outreach, message)
    assert JobRunner().run_once().status == "SUCCEEDED"
    message.refresh_from_db()
    assert message.status == "SENT" and message.provider_message_id.startswith("<")
    assert message.transport_started_at
    assert message.events.filter(event_type="PROVIDER_ACCEPTED").count() == 1
    Job.objects.filter(pk=job.pk).update(status="PENDING")
    assert JobRunner().run_once().status == "SUCCEEDED"
    smtp[1].send_message.assert_called_once()
    outreach["lead"].refresh_from_db()
    outreach["proposal"].refresh_from_db()
    assert outreach["lead"].status == "CONTACTED" and outreach["proposal"].status == "SENT"


def test_both_activation_switches_are_required(outreach, smtp, settings):
    connect(outreach)
    message = approve(outreach, prepare(outreach, real_email=True))
    assert "LIVE_PROVIDER_DISABLED" in eligibility(message)[1]
    live(outreach)
    message.refresh_from_db()
    assert eligibility(message) == ("ELIGIBLE", [])
    settings.OUTREACH_MODE = "simulation"
    assert "LIVE_PROVIDER_DISABLED" in eligibility(message)[1]
    with pytest.raises(ProviderPermanentError):
        outbound_provider("EMAIL", message=message)
    smtp[0].assert_not_called()


def test_simulation_remains_mock_even_with_live_workspace(outreach, smtp, settings):
    connect(outreach)
    live(outreach)
    settings.OUTREACH_MODE = "simulation"
    settings.EMAIL_PROVIDER = "mock"
    message = approve(outreach, prepare(outreach))
    queue(outreach, message)
    assert JobRunner().run_once().status == "SUCCEEDED"
    smtp[0].assert_not_called()


@pytest.mark.parametrize(
    "change", ["disconnect", "sender", "credentials", "workspace_live", "pause", "suppression"]
)
def test_policy_rechecked_before_live_transport(outreach, smtp, settings, change):
    connection = connect(outreach)
    live(outreach)
    message = approve(outreach, prepare(outreach, real_email=True))
    queue(outreach, message)
    member = outreach["member"]
    if change == "disconnect":
        connect(outreach, enabled=False, revision=connection.revision)
    elif change == "sender":
        connect(outreach, revision=connection.revision, name="Nombre cambiado")
    elif change == "credentials":
        settings.OUTREACH_SMTP_PASSWORD = "rotated-secret"
    elif change == "workspace_live":
        live(outreach, False)
    elif change == "pause":
        pause(user=member.user, workspace_id=member.workspace_id, paused=True)
    else:
        add_suppression(
            user=member.user,
            workspace_id=member.workspace_id,
            scope="EMAIL",
            value=message.recipient,
            reason="MANUAL_BLOCK",
            source="Fixture",
        )
    assert JobRunner().run_once().status == "SUCCEEDED"
    message.refresh_from_db()
    assert message.status == "SUPPRESSED"
    smtp[0].assert_not_called()


def test_missing_connection_degrades_cleanly(outreach, settings):
    with pytest.raises(ValidationError):
        prepare(outreach, real_email=True)
    with pytest.raises(ValidationError):
        connect(outreach)
    assert not EmailConnection.objects.exists()


def test_unknown_delivery_needs_explicit_reconciliation(outreach, smtp):
    connect(outreach)
    live(outreach)
    smtp[1].send_message.side_effect = TimeoutError("token-secret")
    message = approve(outreach, prepare(outreach, real_email=True))
    job = queue(outreach, message)
    assert JobRunner().run_once().status == "FAILED"
    message.refresh_from_db()
    assert message.status == "UNKNOWN"
    with pytest.raises(ValidationError):
        recover(outreach, message, "retry")
    Job.objects.filter(pk=job.pk).update(status="PENDING")
    assert JobRunner().run_once().status == "FAILED"
    smtp[1].send_message.assert_called_once()
    message = recover(outreach, message, "accepted")
    assert message.status == "SENT"
    smtp[1].send_message.assert_called_once()
    assert Job.objects.get(pk=job.pk).status == "SUCCEEDED"


def test_verified_nonacceptance_can_retry_same_identity(outreach, smtp):
    connect(outreach)
    live(outreach)
    smtp[1].send_message.side_effect = TimeoutError("uncertain")
    message = approve(outreach, prepare(outreach, real_email=True))
    queue(outreach, message)
    assert JobRunner().run_once().status == "FAILED"
    message = recover(outreach, message, "not_accepted")
    assert message.status == "QUEUED" and not message.transport_started_at
    smtp[1].send_message.side_effect = None
    assert JobRunner().run_once().status == "SUCCEEDED"
    calls = smtp[1].send_message.call_args_list
    assert calls[0].args[0]["Message-ID"] == calls[1].args[0]["Message-ID"]


def test_confirmed_temporary_rejection_retries(outreach, smtp):
    connect(outreach)
    live(outreach)
    smtp[1].send_message.side_effect = smtplib.SMTPDataError(451, b"rejected")
    message = approve(outreach, prepare(outreach, real_email=True))
    job = queue(outreach, message)
    assert JobRunner().run_once().status == "PENDING"
    message.refresh_from_db()
    assert message.status == "FAILED" and not message.transport_started_at
    Job.objects.filter(pk=job.pk).update(available_at=timezone.now() - timedelta(seconds=1))
    smtp[1].send_message.side_effect = None
    assert JobRunner().run_once().status == "SUCCEEDED"
    assert smtp[1].send_message.call_count == 2


def test_known_auth_failure_is_visible_safe_and_recoverable(outreach, smtp, caplog):
    connect(outreach)
    live(outreach)
    smtp[1].login.side_effect = smtplib.SMTPAuthenticationError(535, b"fictitious-secret-never-log")
    message = approve(outreach, prepare(outreach, real_email=True))
    queue(outreach, message)
    assert JobRunner().run_once().status == "FAILED"
    message.refresh_from_db()
    assert message.status == "FAILED" and message.error_code == "ProviderAuthError"
    assert "fictitious-secret" not in caplog.text + message.error_message
    recover(outreach, message, "retry")
    smtp[1].login.side_effect = None
    assert JobRunner().run_once().status == "SUCCEEDED"


@pytest.mark.django_db(transaction=True)
def test_acceptance_survives_pause_during_transport(outreach, smtp):
    from django.db import connection

    connect(outreach)
    live(outreach)
    member = outreach["member"]

    def accepted(*args, **kwargs):
        assert not connection.in_atomic_block
        pause(user=member.user, workspace_id=member.workspace_id, paused=True)
        return {}

    smtp[1].send_message.side_effect = accepted
    message = approve(outreach, prepare(outreach, real_email=True))
    queue(outreach, message)
    assert JobRunner().run_once().status == "SUCCEEDED"
    message.refresh_from_db()
    assert message.status == "SENT" and message.provider_message_id


def test_connection_ui_is_tenant_scoped_csrf_protected_and_secret_free(outreach, smtp):
    member = outreach["member"]
    client = Client(enforce_csrf_checks=True)
    client.force_login(member.user)
    url = reverse("outreach:email_connection", args=[member.workspace_id])
    page = client.get(url)
    assert page.status_code == 200 and b"fictitious-secret" not in page.content
    assert not EmailConnection.objects.exists()
    assert client.post(url, {"enabled": "on", "expected_revision": 0}).status_code == 403
    token = client.cookies["csrftoken"].value
    assert (
        client.post(
            url,
            {"enabled": "on", "sender_name": "Fixture", "expected_revision": 0},
            HTTP_X_CSRFTOKEN=token,
        ).status_code
        == 302
    )
    other = outreach["foreign"]
    client.force_login(other.user)
    assert client.get(url).status_code == 404
    with pytest.raises(Http404):
        configure_connection(
            user=other.user,
            workspace_id=member.workspace_id,
            sender_name="Foreign",
            enabled=True,
            expected_revision=1,
        )
    assert not AuditEvent.objects.filter(metadata__icontains="fictitious-secret").exists()


def test_viewer_cannot_authorize_connection(outreach, smtp):
    member = MembershipFactory(workspace=outreach["member"].workspace, role="VIEWER")
    with pytest.raises(PermissionDenied):
        configure_connection(
            user=member.user,
            workspace_id=member.workspace_id,
            sender_name="Viewer",
            enabled=True,
            expected_revision=0,
        )
    assert not EmailConnection.objects.exists()


def test_stale_reservation_without_receipt_never_submits(outreach, smtp):
    connect(outreach)
    live(outreach)
    message = approve(outreach, prepare(outreach, real_email=True))
    queue(outreach, message)
    OutreachMessage.objects.filter(pk=message.pk).update(
        status="SENDING", transport_started_at=timezone.now()
    )
    assert JobRunner().run_once().status == "FAILED"
    message.refresh_from_db()
    assert message.status == "UNKNOWN"
    smtp[0].assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_receipt_survives_account_revocation_and_crm_can_be_reconciled(outreach, smtp):
    member = outreach["member"]
    replacement = MembershipFactory(workspace=member.workspace, role="OWNER")
    connect(outreach)
    live(outreach)

    def accepted(*args, **kwargs):
        type(member.user).objects.filter(pk=member.user_id).update(is_active=False)
        return {}

    smtp[1].send_message.side_effect = accepted
    message = approve(outreach, prepare(outreach, real_email=True))
    queue(outreach, message)
    assert JobRunner().run_once().status == "SUCCEEDED"
    message.refresh_from_db()
    outreach["lead"].refresh_from_db()
    assert message.status == "SENT" and outreach["lead"].last_contact_at
    assert AuditEvent.objects.filter(action="MESSAGE_STATE_RECONCILIATION_REQUIRED").exists()
    recover_email(
        user=replacement.user,
        workspace_id=member.workspace_id,
        pk=message.pk,
        action="sync",
        evidence="Aceptación ya registrada",
        confirmed=True,
    )
    outreach["lead"].refresh_from_db()
    outreach["proposal"].refresh_from_db()
    assert outreach["lead"].status == "CONTACTED" and outreach["proposal"].status == "SENT"
    smtp[1].send_message.assert_called_once()


def test_receipt_persistence_failure_does_not_resubmit(outreach, smtp):
    connect(outreach)
    live(outreach)
    message = approve(outreach, prepare(outreach, real_email=True))
    queue(outreach, message)
    with patch(
        "apps.outreach.services.record_smtp_receipt", side_effect=RuntimeError("DB failure")
    ):
        assert JobRunner().run_once().status == "FAILED"
    message.refresh_from_db()
    assert message.status == "UNKNOWN"
    recover(outreach, message, "accepted")
    smtp[1].send_message.assert_called_once()


def test_recovery_cannot_bypass_pause_or_policy(outreach, smtp):
    connect(outreach)
    live(outreach)
    message = approve(outreach, prepare(outreach, real_email=True))
    queue(outreach, message)
    smtp[1].send_message.side_effect = TimeoutError()
    assert JobRunner().run_once().status == "FAILED"
    member = outreach["member"]
    pause(user=member.user, workspace_id=member.workspace_id, paused=True)
    with pytest.raises(ValidationError):
        recover(outreach, message, "not_accepted")
    assert OutreachMessage.objects.get(pk=message.pk).status == "UNKNOWN"
    # Pausing future work cannot erase a server-confirmed acceptance.
    assert recover(outreach, message, "accepted").status == "SENT"


def test_connection_revision_validation_and_live_ui(outreach, smtp):
    member = outreach["member"]
    connection = connect(outreach)
    with pytest.raises(ValidationError):
        connect(outreach, revision=0)
    with pytest.raises(ValidationError):
        connect(outreach, revision=connection.revision, name="Agency\nBcc")
    with pytest.raises(ValidationError):
        set_live_mode(
            user=member.user, workspace_id=member.workspace_id, enabled="yes", expected_revision=0
        )
    with pytest.raises(ValidationError):
        set_live_mode(
            user=member.user, workspace_id=member.workspace_id, enabled=True, expected_revision=999
        )
    client = Client()
    client.force_login(member.user)
    url = reverse("outreach:email_connection", args=[member.workspace_id])
    config = OutreachSettings.objects.get(workspace=member.workspace)
    assert (
        client.post(
            url, {"action": "live", "enabled": "on", "expected_revision": config.revision}
        ).status_code
        == 302
    )
    assert (
        client.post(
            url, {"action": "live", "enabled": "on", "expected_revision": config.revision}
        ).status_code
        == 400
    )
    assert OutreachSettings.objects.get(pk=config.pk).live_mode_enabled
    assert (
        client.post(url, {"sender_name": "x", "enabled": "on", "expected_revision": 0}).status_code
        == 400
    )
    assert client.post(url, {"expected_revision": "bad"}).status_code == 400


def test_recovery_ui_and_explicit_confirmation(outreach, smtp):
    connect(outreach)
    live(outreach)
    message = approve(outreach, prepare(outreach, real_email=True))
    queue(outreach, message)
    smtp[1].send_message.side_effect = TimeoutError()
    assert JobRunner().run_once().status == "FAILED"
    member = outreach["member"]
    client = Client()
    client.force_login(member.user)
    url = reverse("outreach:message", args=[member.workspace_id, message.pk])
    page = client.get(url)
    assert b"Message-ID" in page.content and b"agencycrm.local" in page.content
    assert client.post(url, {"action": "recover", "outcome": "accepted"}).status_code == 400
    assert (
        client.post(
            url,
            {
                "action": "recover",
                "outcome": "accepted",
                "confirmed": "on",
                "evidence": "Registro SMTP ficticio",
            },
        ).status_code
        == 302
    )
    assert OutreachMessage.objects.get(pk=message.pk).status == "SENT"
    with pytest.raises(ValidationError):
        recover_email(
            user=member.user,
            workspace_id=member.workspace_id,
            pk=message.pk,
            action="retry",
            evidence="",
            confirmed=False,
        )


def test_transport_mode_cannot_reuse_preparation_key(outreach, smtp):
    connect(outreach)
    original = prepare(outreach, key="same", real_email=True)
    assert prepare(outreach, key="same", real_email=True).pk == original.pk
    with pytest.raises(ValidationError):
        prepare(outreach, key="same", real_email=False)
    with pytest.raises(ValidationError):
        prepare(outreach, channel="WHATSAPP", real_email=True)
    with pytest.raises(ValidationError):
        prepare(outreach, real_email="yes")


def test_direct_factory_requires_workspace_authorization(outreach, smtp):
    connect(outreach)
    message = prepare(outreach, real_email=True)
    with pytest.raises(EmailConfigurationError):
        outbound_provider("EMAIL", message=message)
    live(outreach)
    assert isinstance(outbound_provider("EMAIL", message=message), SMTPEmailProvider)
    with pytest.raises(EmailConfigurationError):
        outbound_provider("EMAIL", message=message, failure="temporary")
    with pytest.raises(ProviderPermanentError):
        outbound_provider("WHATSAPP", message=message)
    smtp[0].assert_not_called()
