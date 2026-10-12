import hashlib
import hmac
import json
import logging
import uuid
from dataclasses import replace
from datetime import timedelta
from email.utils import format_datetime
from unittest.mock import patch

import httpx
import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.compliance.policies import eligibility
from apps.compliance.services import add_suppression
from apps.jobs.models import Job
from apps.jobs.runner import JobRunner
from apps.outreach.email_recovery import recover_email
from apps.outreach.email_services import set_live_mode
from apps.outreach.models import (
    InboundWhatsAppMessage,
    OutreachMessage,
    OutreachSettings,
    WhatsAppConnection,
)
from apps.outreach.services import pause
from apps.outreach.whatsapp_services import configure_connection, create_template, disable_template
from common.logging import WebhookQueryFilter
from integrations.common.contracts import (
    SendAttachment,
    SendMessageRequest,
    WhatsAppTemplatePayload,
)
from integrations.common.errors import (
    ProviderDeliveryUnknownError,
    ProviderPermanentError,
    ProviderRateLimitError,
    WhatsAppConfigurationError,
)
from integrations.common.factory import outbound_provider
from integrations.whatsapp.provider import WhatsAppCloudProvider
from tests.factories import MembershipFactory
from tests.test_outreach import approve, prepare, queue
from tests.test_outreach import domain as domain
from tests.test_outreach import outreach as outreach
from tests.test_outreach import proposals as proposals

pytestmark = pytest.mark.django_db
WAMID = "wamid.FICTITIOUS_1+=/"


@pytest.fixture
def cloud(settings):
    settings.OUTREACH_MODE = "live"
    settings.WHATSAPP_PROVIDER = "cloud"
    settings.WHATSAPP_PHONE_NUMBER_ID = "123456789"
    settings.WHATSAPP_WABA_ID = "987654321"
    settings.WHATSAPP_GRAPH_API_VERSION = (
        "v99.0"  # Fictional configurable fixture, not a live recommendation.
    )
    settings.WHATSAPP_ACCESS_TOKEN = "fictitious-access-secret"
    settings.WHATSAPP_APP_SECRET = "fictitious-app-secret-12345678901234567890"
    settings.WHATSAPP_VERIFY_TOKEN = "fictitious-verify-token-12345678901234567890"
    settings.WHATSAPP_WEBHOOK_ENABLED = True
    settings.WHATSAPP_HTTP_TIMEOUT = 10
    requests = []
    state = {"status": 200, "receipt": WAMID, "during": None, "error": None, "media_status": 200}

    def respond(request):
        requests.append(request)
        assert request.url.host == "graph.facebook.com" and request.url.scheme == "https"
        if request.url.path.endswith("/media"):
            return httpx.Response(state["media_status"], json={"id": "111222333"})
        if state["during"]:
            state["during"](request)
        if state["error"]:
            raise state["error"]
        return httpx.Response(
            state["status"],
            headers={"Retry-After": "60"},
            json={"messages": [{"id": state["receipt"]}]},
        )

    transport = httpx.MockTransport(respond)

    def select(channel, **kwargs):
        provider = outbound_provider(channel, **kwargs)
        provider.transport = transport
        return provider

    with patch("apps.outreach.services.outbound_provider", side_effect=select):
        yield {"transport": transport, "requests": requests, "state": state}


def connect(env):
    member = env["member"]
    return configure_connection(
        user=member.user, workspace_id=member.workspace_id, enabled=True, expected_revision=0
    )


def template(env, *, document=False, name="agency_proposal"):
    member = env["member"]
    return create_template(
        user=member.user,
        workspace_id=member.workspace_id,
        data={
            "name": name,
            "language": "es",
            "category": "MARKETING",
            "body_template": "Hola {{1}}, propuesta de {{2}}.",
            "document_header": document,
            "approved_in_meta": True,
        },
    )


def configure(env, *, document=False):
    connection = connect(env)
    item = template(env, document=document)
    member = env["member"]
    config = OutreachSettings.objects.get(workspace_id=member.workspace_id)
    set_live_mode(
        user=member.user,
        workspace_id=member.workspace_id,
        enabled=True,
        expected_revision=config.revision,
    )
    return connection, item


def message(env, item):
    return approve(
        env,
        prepare(
            env,
            channel="WHATSAPP",
            real_whatsapp=True,
            whatsapp_template_id=item.pk,
            template_parameters=["Clínica ficticia", "SEO Local"],
        ),
    )


def payload(env, *, statuses=None, messages=None, phone="123456789", waba="987654321"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": waba,
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": phone},
                            "statuses": statuses or [],
                            "messages": messages or [],
                        },
                    }
                ],
            }
        ],
    }


def status(value="delivered", *, identity=WAMID, correlation=None, recipient="34600123456"):
    result = {
        "id": identity,
        "status": value,
        "timestamp": str(int(timezone.now().timestamp())),
        "recipient_id": recipient,
    }
    if correlation:
        result["biz_opaque_callback_data"] = str(correlation)
    if value == "failed":
        result["errors"] = [{"code": 131026, "message": "secret-payload-never-store"}]
    return result


def webhook(env, value, settings, *, signature=None, client=None):
    raw = json.dumps(value).encode()
    signature = (
        signature
        or "sha256="
        + hmac.new(settings.WHATSAPP_APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    )
    url = reverse("whatsapp_webhook", args=[env["member"].workspace_id])
    return (client or Client(enforce_csrf_checks=True)).post(
        url, raw, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=signature
    )


def test_template_send_keeps_provider_id_and_idempotence(outreach, cloud):
    _, item = configure(outreach)
    outbound = message(outreach, item)
    job = queue(outreach, outbound)
    assert JobRunner().run_once().status == "SUCCEEDED"
    outbound.refresh_from_db()
    assert outbound.status == "SENT" and outbound.provider_message_id == WAMID
    sent = json.loads(cloud["requests"][0].content)
    assert sent["to"] == "34600123456" and sent["type"] == "template"
    assert sent["template"]["name"] == item.name and sent["template"]["language"] == {"code": "es"}
    assert sent["template"]["components"][0]["parameters"][0]["text"] == "Clínica ficticia"
    assert sent["biz_opaque_callback_data"] == str(outbound.pk)
    assert "attachments" not in sent and "body_text" not in sent
    assert cloud["requests"][0].headers["Authorization"] == "Bearer fictitious-access-secret"
    Job.objects.filter(pk=job.pk).update(status="PENDING")
    assert JobRunner().run_once().status == "SUCCEEDED"
    assert len(cloud["requests"]) == 1
    outreach["proposal"].refresh_from_db()
    assert outreach["proposal"].status == "SENT"


def test_private_document_header_is_uploaded_before_template(outreach, cloud):
    _, item = configure(outreach, document=True)
    outbound = message(outreach, item)
    queue(outreach, outbound)
    assert JobRunner().run_once().status == "SUCCEEDED"
    requests = cloud["requests"]
    assert requests[0].url.path.endswith("/media") and b"%PDF" in requests[0].content
    assert requests[1].url.path.endswith("/messages")
    component = json.loads(requests[1].content)["template"]["components"][1]
    assert component["type"] == "header"
    assert component["parameters"][0]["document"]["id"] == "111222333"
    assert "link" not in component["parameters"][0]["document"]


@pytest.mark.parametrize(
    "change",
    [
        "pause",
        "suppression",
        "daily_limit",
        "template",
        "connection",
        "credentials",
        "workspace_live",
        "global_mode",
    ],
)
def test_controls_block_cloud_before_transport(outreach, cloud, settings, change):
    connection, item = configure(outreach)
    outbound = message(outreach, item)
    queue(outreach, outbound)
    member = outreach["member"]
    if change == "pause":
        pause(user=member.user, workspace_id=member.workspace_id, paused=True)
    elif change == "suppression":
        add_suppression(
            user=member.user,
            workspace_id=member.workspace_id,
            scope="PHONE",
            value=outbound.recipient,
            reason="MANUAL_BLOCK",
            source="Fixture",
        )
    elif change == "daily_limit":
        OutreachSettings.objects.filter(workspace_id=member.workspace_id).update(
            daily_whatsapp_limit=0
        )
    elif change == "template":
        disable_template(user=member.user, workspace_id=member.workspace_id, pk=item.pk)
    elif change == "connection":
        configure_connection(
            user=member.user,
            workspace_id=member.workspace_id,
            enabled=False,
            expected_revision=connection.revision,
        )
    elif change == "credentials":
        settings.WHATSAPP_ACCESS_TOKEN = "rotated"
    elif change == "workspace_live":
        OutreachSettings.objects.filter(workspace_id=member.workspace_id).update(
            live_mode_enabled=False
        )
    else:
        settings.OUTREACH_MODE = "simulation"
    assert JobRunner().run_once().status == "SUCCEEDED"
    outbound.refresh_from_db()
    assert outbound.status == "SUPPRESSED" and not cloud["requests"]


def test_simulation_never_constructs_cloud_even_when_workspace_enabled(outreach, cloud, settings):
    configure(outreach)
    settings.OUTREACH_MODE = "simulation"
    settings.WHATSAPP_PROVIDER = "mock"
    with patch("integrations.whatsapp.provider.WhatsAppCloudProvider", side_effect=AssertionError):
        outbound = approve(outreach, prepare(outreach, channel="WHATSAPP"))
        queue(outreach, outbound)
        assert JobRunner().run_once().status == "SUCCEEDED"
    assert not cloud["requests"]


@pytest.mark.parametrize(
    ("http_status", "expected"),
    [
        (401, "FAILED"),
        (403, "FAILED"),
        (400, "FAILED"),
        (429, "PENDING"),
        (500, "FAILED"),
        (302, "FAILED"),
    ],
)
def test_cloud_errors_are_normalized_without_blind_retries(
    outreach, cloud, http_status, expected, caplog
):
    _, item = configure(outreach)
    outbound = message(outreach, item)
    job = queue(outreach, outbound)
    cloud["state"]["status"] = http_status
    result = JobRunner().run_once()
    assert result.status == expected
    outbound.refresh_from_db()
    assert outbound.status == ("UNKNOWN" if http_status in {500, 302} else "FAILED")
    assert "fictitious-access-secret" not in result.last_error + caplog.text
    if http_status == 429:
        assert (result.available_at - timezone.now()).total_seconds() > 50
        Job.objects.filter(pk=job.pk).update(available_at=timezone.now() - timedelta(seconds=1))
        cloud["state"]["status"] = 200
        assert JobRunner().run_once().status == "SUCCEEDED"


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("secret"), httpx.WriteError("secret")])
def test_uncertain_submission_waits_for_signed_callback(outreach, cloud, settings, failure):
    _, item = configure(outreach)
    outbound = message(outreach, item)
    job = queue(outreach, outbound)
    cloud["state"]["error"] = failure
    assert JobRunner().run_once().status == "FAILED"
    outbound.refresh_from_db()
    assert outbound.status == "UNKNOWN"
    Job.objects.filter(pk=job.pk).update(status="PENDING")
    assert JobRunner().run_once().status == "FAILED"
    assert len(cloud["requests"]) == 1
    response = webhook(
        outreach,
        payload(outreach, statuses=[status("delivered", correlation=outbound.pk)]),
        settings,
    )
    assert response.status_code == 200
    outbound.refresh_from_db()
    assert outbound.status == "DELIVERED" and outbound.provider_message_id == WAMID
    outreach["proposal"].refresh_from_db()
    assert outreach["proposal"].status == "SENT"
    Job.objects.filter(pk=job.pk).update(status="PENDING")
    assert JobRunner().run_once().status == "SUCCEEDED"
    assert len(cloud["requests"]) == 1


def test_invalid_success_receipt_is_uncertain(outreach, cloud):
    _, item = configure(outreach)
    outbound = message(outreach, item)
    queue(outreach, outbound)
    cloud["state"]["receipt"] = "not-an-id"
    assert JobRunner().run_once().status == "FAILED"
    assert OutreachMessage.objects.get(pk=outbound.pk).status == "UNKNOWN"


def test_subscription_verification_and_invalid_signature_do_not_mutate(outreach, cloud, settings):
    connect(outreach)
    url = reverse("whatsapp_webhook", args=[outreach["member"].workspace_id])
    client = Client(enforce_csrf_checks=True)
    assert (
        client.get(
            url,
            {
                "hub.mode": "subscribe",
                "hub.verify_token": settings.WHATSAPP_VERIFY_TOKEN,
                "hub.challenge": "12345",
            },
        ).content
        == b"12345"
    )
    assert (
        client.get(
            url, {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"}
        ).status_code
        == 403
    )
    assert (
        webhook(outreach, payload(outreach), settings, signature="sha256=wrong").status_code == 403
    )
    assert (
        webhook(outreach, payload(outreach), settings, signature="non-ascii-ñ").status_code == 403
    )
    assert client.post(url, {}, content_type="application/json").status_code == 403
    settings.WHATSAPP_WEBHOOK_ENABLED = False
    assert webhook(outreach, payload(outreach), settings).status_code == 403
    assert not InboundWhatsAppMessage.objects.exists()


def test_duplicate_and_out_of_order_statuses_do_not_regress(outreach, cloud, settings):
    _, item = configure(outreach)
    outbound = message(outreach, item)
    queue(outreach, outbound)
    assert JobRunner().run_once().status == "SUCCEEDED"
    body = payload(
        outreach, statuses=[status("read"), status("delivered"), status("sent"), status("failed")]
    )
    for _ in range(2):
        assert webhook(outreach, body, settings).status_code == 200
    outbound.refresh_from_db()
    assert outbound.status == "READ" and outbound.delivered_at
    assert outbound.events.filter(event_type="READ").count() == 1
    assert outbound.events.filter(event_type="DELIVERED").count() == 1
    assert outbound.events.filter(event_type="FAILED").count() == 1
    assert "secret-payload" not in str(list(outbound.events.values_list("payload", flat=True)))


def test_delivery_failure_cannot_be_replayed(outreach, cloud, settings):
    _, item = configure(outreach)
    outbound = message(outreach, item)
    job = queue(outreach, outbound)
    assert JobRunner().run_once().status == "SUCCEEDED"
    assert (
        webhook(outreach, payload(outreach, statuses=[status("failed")]), settings).status_code
        == 200
    )
    outbound.refresh_from_db()
    assert outbound.status == "FAILED" and outbound.error_code == "WHATSAPP_DELIVERY_FAILED"
    Job.objects.filter(pk=job.pk).update(status="PENDING")
    assert JobRunner().run_once().status == "SUCCEEDED"
    assert len(cloud["requests"]) == 1


def test_inbound_context_is_scoped_idempotent_and_escaped(outreach, cloud, settings):
    _, item = configure(outreach)
    outbound = message(outreach, item)
    queue(outreach, outbound)
    assert JobRunner().run_once().status == "SUCCEEDED"
    inbound = {
        "id": "wamid.FICTITIOUS_REPLY",
        "from": "34600123456",
        "timestamp": str(int(timezone.now().timestamp())),
        "type": "text",
        "text": {"body": "<script>alert('fixture')</script>"},
        "context": {"id": WAMID},
    }
    for _ in range(2):
        assert webhook(outreach, payload(outreach, messages=[inbound]), settings).status_code == 200
    assert InboundWhatsAppMessage.objects.count() == 1
    received = InboundWhatsAppMessage.objects.get()
    assert received.reply_to_id == outbound.pk and received.contact_id == outreach["contact"].pk
    outbound.refresh_from_db()
    outreach["lead"].refresh_from_db()
    assert outbound.status == "REPLIED" and outreach["lead"].status == "REPLIED"
    client = Client()
    client.force_login(outreach["member"].user)
    url = reverse("outreach:whatsapp_inbox", args=[outreach["member"].workspace_id])
    content = client.get(url).content
    assert b"&lt;script&gt;" in content and b"<script>alert" not in content
    client.force_login(outreach["foreign"].user)
    assert client.get(url).status_code == 404


@pytest.mark.parametrize("mismatch", ["phone", "waba", "recipient", "correlation"])
def test_signed_foreign_callback_cannot_cross_workspace_or_recipient(
    outreach, cloud, settings, mismatch
):
    _, item = configure(outreach)
    outbound = message(outreach, item)
    queue(outreach, outbound)
    cloud["state"]["error"] = httpx.ReadTimeout("fixture timeout")
    assert JobRunner().run_once().status == "FAILED"
    kwargs = {mismatch: "999999999"} if mismatch in {"phone", "waba"} else {}
    event = status(correlation=outbound.pk)
    if mismatch == "recipient":
        event["recipient_id"] = "34699123456"
    if mismatch == "correlation":
        event["biz_opaque_callback_data"] = str(uuid.uuid4())
    assert (
        webhook(outreach, payload(outreach, statuses=[event], **kwargs), settings).status_code
        == 200
    )
    assert OutreachMessage.objects.get(pk=outbound.pk).status == "UNKNOWN"


@pytest.mark.django_db(transaction=True)
def test_callback_before_http_response_preserves_read_state(outreach, cloud, settings):
    from django.db import connection

    _, item = configure(outreach)
    outbound = message(outreach, item)
    queue(outreach, outbound)

    def during(request):
        assert not connection.in_atomic_block
        assert (
            webhook(
                outreach,
                payload(outreach, statuses=[status("read", correlation=outbound.pk)]),
                settings,
            ).status_code
            == 200
        )

    cloud["state"]["during"] = during
    assert JobRunner().run_once().status == "SUCCEEDED"
    outbound.refresh_from_db()
    assert outbound.status == "READ"
    assert outbound.events.filter(event_type="SENT").count() == 1


def test_connection_and_template_ui_preserves_csrf_and_permissions(outreach, cloud):
    member = outreach["member"]
    client = Client(enforce_csrf_checks=True)
    client.force_login(member.user)
    url = reverse("outreach:whatsapp_connection", args=[member.workspace_id])
    assert client.get(url).status_code == 200
    assert not WhatsAppConnection.objects.exists()
    assert (
        client.post(
            url, {"action": "connection", "enabled": "on", "expected_revision": 0}
        ).status_code
        == 403
    )
    token = client.cookies["csrftoken"].value
    assert (
        client.post(
            url,
            {"action": "connection", "enabled": "on", "expected_revision": 0},
            HTTP_X_CSRFTOKEN=token,
        ).status_code
        == 302
    )
    page = client.get(url)
    assert (
        b"fictitious-access-secret" not in page.content
        and b"fictitious-app-secret" not in page.content
    )
    item = template(outreach)
    assert (
        client.post(
            url, {"action": "disable_template", "template_id": item.pk}, HTTP_X_CSRFTOKEN=token
        ).status_code
        == 302
    )
    viewer = MembershipFactory(workspace=member.workspace, role="VIEWER")
    with pytest.raises(PermissionDenied):
        configure_connection(
            user=viewer.user, workspace_id=member.workspace_id, enabled=True, expected_revision=1
        )
    with pytest.raises(Http404):
        disable_template(
            user=outreach["foreign"].user, workspace_id=member.workspace_id, pk=item.pk
        )


def test_missing_credentials_and_invalid_template_data_fail_cleanly(outreach, cloud, settings):
    connection = connect(outreach)
    with pytest.raises(ValidationError):
        create_template(user=outreach["member"].user, workspace_id=connection.workspace_id, data={})
    item = template(outreach)
    with pytest.raises(ValidationError):
        prepare(
            outreach,
            channel="WHATSAPP",
            real_whatsapp=True,
            whatsapp_template_id=item.pk,
            template_parameters=["missing"],
        )
    settings.WHATSAPP_ACCESS_TOKEN = ""
    with pytest.raises(ValidationError):
        prepare(
            outreach,
            channel="WHATSAPP",
            real_whatsapp=True,
            whatsapp_template_id=item.pk,
            template_parameters=["a", "b"],
        )
    with pytest.raises(WhatsAppConfigurationError):
        WhatsAppCloudProvider(transport=cloud["transport"]).send(
            SendMessageRequest(uuid.uuid4(), "key", "+34600123456", "body")
        )
    assert not cloud["requests"]


def test_machine_body_limits_and_bad_signed_payload_are_rejected(outreach, cloud, settings):
    connect(outreach)
    assert webhook(outreach, {"object": "other"}, settings).status_code == 400
    assert (
        webhook(
            outreach, payload(outreach, statuses=[status() for _ in range(51)]), settings
        ).status_code
        == 400
    )
    assert (
        webhook(
            outreach, {"object": "whatsapp_business_account", "entry": "bad"}, settings
        ).status_code
        == 400
    )
    assert webhook(outreach, {"large": "x" * 131073}, settings).status_code == 403


def test_query_secrets_are_removed_from_access_logs():
    record = logging.LogRecord(
        "django.server",
        logging.INFO,
        "file",
        1,
        "GET /integrations/whatsapp/uuid/webhook/?hub.verify_token=secret&hub.challenge=1 HTTP/1.1",
        (),
        None,
    )
    assert WebhookQueryFilter().filter(record)
    assert "secret" not in record.getMessage() and "[redacted]" in record.getMessage()


def test_real_factory_requires_workspace_and_current_configuration(outreach, cloud):
    connection = connect(outreach)
    item = template(outreach)
    outbound = message(outreach, item)
    with pytest.raises(WhatsAppConfigurationError):
        outbound_provider("WHATSAPP", message=outbound)
    config = OutreachSettings.objects.get(workspace=connection.workspace)
    set_live_mode(
        user=outreach["member"].user,
        workspace_id=connection.workspace_id,
        enabled=True,
        expected_revision=config.revision,
    )
    assert isinstance(outbound_provider("WHATSAPP", message=outbound), WhatsAppCloudProvider)
    assert eligibility(outbound) == ("ELIGIBLE", [])
    with pytest.raises(WhatsAppConfigurationError):
        outbound_provider("WHATSAPP", message=outbound, failure="temporary")


def test_verified_manual_cloud_reconciliation_requires_wamid(outreach, cloud):
    _, item = configure(outreach)
    outbound = message(outreach, item)
    queue(outreach, outbound)
    cloud["state"]["error"] = httpx.ReadTimeout("fixture timeout")
    assert JobRunner().run_once().status == "FAILED"
    member = outreach["member"]
    arguments = dict(
        user=member.user,
        workspace_id=member.workspace_id,
        pk=outbound.pk,
        action="accepted",
        evidence="Comprobación ficticia en Meta",
        confirmed=True,
    )
    with pytest.raises(ValidationError):
        recover_email(**arguments)
    assert recover_email(**arguments, provider_message_id=WAMID).status == "SENT"
    assert len(cloud["requests"]) == 1


def test_phone_assignment_cannot_be_reused_by_another_workspace(outreach, cloud):
    connect(outreach)
    foreign = outreach["foreign"]
    with pytest.raises(ValidationError):
        configure_connection(
            user=foreign.user, workspace_id=foreign.workspace_id, enabled=True, expected_revision=0
        )
    assert WhatsAppConnection.objects.count() == 1


@pytest.mark.parametrize(
    "response", [b"not json", b"[]", b"x" * 65537], ids=["invalid-json", "array", "oversize"]
)
def test_malformed_or_oversize_success_cannot_be_retried(cloud, response):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=response))
    request = SendMessageRequest(
        uuid.uuid4(),
        "key",
        "+34600123456",
        "body",
        whatsapp_template=WhatsAppTemplatePayload("fixture", "es", ("parameter",)),
        correlation_id=str(uuid.uuid4()),
    )
    with pytest.raises(ProviderDeliveryUnknownError):
        WhatsAppCloudProvider(transport=transport).send(request)


def test_media_failure_is_safe_to_retry_without_message_submission(outreach, cloud):
    _, item = configure(outreach, document=True)
    outbound = message(outreach, item)
    queue(outreach, outbound)
    cloud["state"]["media_status"] = 500
    assert JobRunner().run_once().status == "PENDING"
    outbound.refresh_from_db()
    assert outbound.status == "FAILED" and not outbound.transport_started_at
    assert len(cloud["requests"]) == 1 and cloud["requests"][0].url.path.endswith("/media")


def test_template_schema_and_connection_updates_are_checked(outreach, cloud, settings):
    connection = connect(outreach)
    member = outreach["member"]
    with pytest.raises(ValidationError):
        create_template(
            user=member.user,
            workspace_id=member.workspace_id,
            data={
                "name": "invalid",
                "language": "es",
                "category": "MARKETING",
                "body_template": "Hola {{2}}",
                "document_header": False,
                "approved_in_meta": True,
            },
        )
    with pytest.raises(ValidationError):
        configure_connection(
            user=member.user, workspace_id=member.workspace_id, enabled=False, expected_revision=0
        )
    assert (
        configure_connection(
            user=member.user,
            workspace_id=member.workspace_id,
            enabled=True,
            expected_revision=connection.revision,
        ).revision
        == 2
    )
    settings.WHATSAPP_PHONE_NUMBER_ID = "222222222"
    with pytest.raises(ValidationError):
        configure_connection(
            user=member.user, workspace_id=member.workspace_id, enabled=True, expected_revision=2
        )


def test_connection_ui_registers_templates_and_live_permission(outreach, cloud):
    member = outreach["member"]
    connect(outreach)
    client = Client()
    client.force_login(member.user)
    url = reverse("outreach:whatsapp_connection", args=[member.workspace_id])
    data = {
        "action": "template",
        "name": "ui_fixture",
        "language": "es",
        "category": "MARKETING",
        "body_template": "Hola {{1}}",
        "approved_in_meta": "on",
    }
    assert client.post(url, data).status_code == 302
    assert client.post(url, data).status_code == 400
    config = OutreachSettings.objects.get(workspace_id=member.workspace_id)
    assert (
        client.post(
            url, {"action": "live", "enabled": "on", "expected_revision": config.revision}
        ).status_code
        == 302
    )
    assert (
        client.post(url, {"action": "live", "enabled": "on", "expected_revision": 999}).status_code
        == 400
    )
    assert client.post(url, {"action": "unknown"}).status_code == 400
    assert client.post(url, {"action": "disable_template", "template_id": "bad"}).status_code == 400


def test_raw_body_tampering_cannot_use_a_valid_signature(outreach, cloud, settings):
    connect(outreach)
    raw = json.dumps(payload(outreach)).encode()
    signature = (
        "sha256=" + hmac.new(settings.WHATSAPP_APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    )
    url = reverse("whatsapp_webhook", args=[outreach["member"].workspace_id])
    client = Client(enforce_csrf_checks=True)
    assert (
        client.post(
            url, raw + b" ", content_type="application/json", HTTP_X_HUB_SIGNATURE_256=signature
        ).status_code
        == 403
    )
    foreign_url = reverse("whatsapp_webhook", args=[outreach["foreign"].workspace_id])
    assert (
        client.post(
            foreign_url, raw, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=signature
        ).status_code
        == 403
    )
    assert client.delete(url).status_code == 403


def test_cloud_preparation_form_renders_template_content(outreach, cloud):
    _, item = configure(outreach)
    member = outreach["member"]
    client = Client()
    client.force_login(member.user)
    url = reverse("outreach:prepare", args=[member.workspace_id])
    response = client.post(
        url,
        {
            "proposal": outreach["proposal"].pk,
            "contact": outreach["contact"].pk,
            "channel": "WHATSAPP",
            "real_whatsapp": "on",
            "whatsapp_template": item.pk,
            "template_parameters": '["Empresa", "SEO"]',
            "idempotency_key": uuid.uuid4(),
        },
    )
    assert response.status_code == 302
    outbound = OutreachMessage.objects.get(provider="cloud")
    assert outbound.body_text == "Hola Empresa, propuesta de SEO."
    assert b"WhatsApp real" in client.get(response.url).content


@pytest.mark.parametrize(
    "invalid", ["recipient", "name", "parameter", "correlation", "missing-document", "extra-file"]
)
def test_invalid_transport_input_never_reaches_graph(cloud, invalid):
    request = SendMessageRequest(
        uuid.uuid4(),
        "key",
        "+34600123456",
        "body",
        whatsapp_template=WhatsAppTemplatePayload("fixture", "es", ("text",)),
        correlation_id=str(uuid.uuid4()),
    )
    changes = {
        "recipient": {"recipient": "not-a-phone"},
        "name": {"whatsapp_template": WhatsAppTemplatePayload("../invalid", "es")},
        "parameter": {"whatsapp_template": WhatsAppTemplatePayload("fixture", "es", (123,))},
        "correlation": {"correlation_id": "not-a-uuid"},
        "missing-document": {
            "whatsapp_template": WhatsAppTemplatePayload("fixture", "es", (), True)
        },
        "extra-file": {
            "attachments": (SendAttachment("file.pdf", "application/pdf", b"%PDF-fixture"),)
        },
    }
    with pytest.raises(ProviderPermanentError):
        WhatsAppCloudProvider(transport=cloud["transport"]).send(
            replace(request, **changes[invalid])
        )
    assert not cloud["requests"]


def test_graph_retry_after_http_date_is_respected(cloud):
    future = format_datetime(timezone.now() + timedelta(seconds=90), usegmt=True)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(429, headers={"Retry-After": future})
    )
    request = SendMessageRequest(
        uuid.uuid4(),
        "key",
        "+34600123456",
        "body",
        whatsapp_template=WhatsAppTemplatePayload("fixture", "es"),
        correlation_id=str(uuid.uuid4()),
    )
    with pytest.raises(ProviderRateLimitError) as error:
        WhatsAppCloudProvider(transport=transport).send(request)
    assert 85 <= error.value.retry_after <= 90


def test_bad_media_id_does_not_submit_a_message(cloud):
    requests = []

    def invalid_media(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "https://private.invalid/file"})

    request = SendMessageRequest(
        uuid.uuid4(),
        "key",
        "+34600123456",
        "body",
        attachments=(SendAttachment("file.pdf", "application/pdf", b"%PDF-fixture"),),
        whatsapp_template=WhatsAppTemplatePayload("fixture", "es", (), True),
        correlation_id=str(uuid.uuid4()),
    )
    with pytest.raises(ProviderPermanentError):
        WhatsAppCloudProvider(transport=httpx.MockTransport(invalid_media)).send(request)
    assert len(requests) == 1 and requests[0].url.path.endswith("/media")


def test_inbound_media_without_reply_never_fetches_or_infers_a_lead(outreach, cloud, settings):
    connect(outreach)
    received = {
        "id": "wamid.FICTITIOUS_MEDIA",
        "from": "34600123456",
        "type": "document",
        "timestamp": str(int(timezone.now().timestamp())),
        "document": {"id": "private", "url": "http://127.0.0.1/private", "filename": "secret-file"},
    }
    assert webhook(outreach, payload(outreach, messages=[received]), settings).status_code == 200
    record = InboundWhatsAppMessage.objects.get()
    assert record.contact_id == outreach["contact"].pk and record.reply_to_id is None
    assert record.message_type == "document" and record.body_text == ""
    assert not cloud["requests"]
    outreach["lead"].refresh_from_db()
    assert outreach["lead"].status == "READY"


def test_signed_event_with_invalid_timestamp_rolls_back_cleanly(outreach, cloud, settings):
    _, item = configure(outreach)
    outbound = message(outreach, item)
    queue(outreach, outbound)
    assert JobRunner().run_once().status == "SUCCEEDED"
    event = status("read")
    event["timestamp"] = "not-a-date"
    assert webhook(outreach, payload(outreach, statuses=[event]), settings).status_code == 400
    assert not outbound.events.filter(event_type="READ").exists()
    assert OutreachMessage.objects.get(pk=outbound.pk).status == "SENT"


@pytest.mark.django_db(transaction=True)
def test_cloud_acceptance_survives_revocation_during_http(outreach, cloud):
    _, item = configure(outreach)
    outbound = message(outreach, item)
    queue(outreach, outbound)
    member = outreach["member"]

    def revoke(request):
        type(member.user).objects.filter(pk=member.user_id).update(is_active=False)

    cloud["state"]["during"] = revoke
    assert JobRunner().run_once().status == "SUCCEEDED"
    outbound.refresh_from_db()
    assert outbound.status == "SENT" and outbound.provider_message_id == WAMID
    assert len(cloud["requests"]) == 1
