import io
import uuid
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.http import Http404
from django.test import Client
from django.urls import reverse
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from apps.campaigns.models import CampaignService
from apps.jobs.models import Job
from apps.jobs.payloads import validate_payload
from apps.jobs.runner import JobRunner
from apps.proposals.documents import validate_pdf
from apps.proposals.models import Proposal, ProposalAttachment, SalesAsset
from apps.proposals.policies import require_approved
from apps.proposals.services import (
    archive_asset,
    enqueue_proposal,
    generate_proposal,
    private_document,
    transition_proposal,
    upload_asset,
)
from tests.factories import AgencyProfileFactory, ServiceOfferingFactory
from tests.test_phase1 import domain as domain

pytestmark = pytest.mark.django_db


@pytest.fixture
def proposals(domain, settings, tmp_path, monkeypatch):
    settings.MEDIA_ROOT = tmp_path / "media"
    monkeypatch.setattr("apps.jobs.locking.worker_lock_path", lambda: tmp_path / "worker.lock")
    member = domain["member"]
    AgencyProfileFactory(
        workspace=member.workspace,
        trading_name="Agencia Ficticia",
        value_proposition="Captación local con datos contrastados.",
    )
    service = ServiceOfferingFactory(
        workspace=member.workspace, default_pitch="SEO local y revisión inicial."
    )
    CampaignService.objects.create(
        workspace=member.workspace, campaign=domain["campaign"], service=service
    )
    return domain


def payload(domain, provider="template", assets=None):
    return {"lead_id": str(domain["lead"].pk), "provider": provider, "asset_ids": assets or []}


def generate(domain, key=None, **kwargs):
    member = domain["member"]
    return generate_proposal(
        user=member.user,
        workspace_id=member.workspace_id,
        payload=payload(domain, **kwargs),
        idempotency_key=key or str(uuid.uuid4()),
    )


def transition(domain, proposal, target):
    member = domain["member"]
    return transition_proposal(
        user=member.user,
        workspace_id=member.workspace_id,
        pk=proposal.pk,
        target=target,
        expected_revision=proposal.revision,
    )


def sample_pdf():
    output = io.BytesIO()
    doc = canvas.Canvas(output)
    doc.drawString(50, 750, "Fictional agency brochure")
    doc.save()
    return output.getvalue()


def upload(domain, data=None, **kwargs):
    member = domain["member"]
    return upload_asset(
        user=member.user,
        workspace_id=member.workspace_id,
        upload=SimpleUploadedFile(
            "../../brochure.pdf", data or sample_pdf(), content_type="application/pdf"
        ),
        name="Fictional brochure",
        asset_type="BROCHURE",
        **kwargs,
    )


def test_deterministic_generation_pdf_and_safe_preview(proposals, client):
    domain = proposals
    domain["company"].name = '<script>alert("xss")</script> Clínica Norte'
    domain["company"].save()
    first = generate(domain, "same")
    assert first.status == "GENERATED" and first.generated_by == "TEMPLATE"
    assert generate(domain, "same").pk == first.pk
    member = domain["member"]
    data = private_document(
        user=member.user, workspace_id=member.workspace_id, pk=first.pk
    ).getvalue()
    reader = PdfReader(io.BytesIO(data), strict=True)
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert "Resumen ejecutivo" in text and "SEO local" in text
    assert "Clínica Norte" in text and "Próximos pasos" in text
    assert len(reader.pages) >= 1 and not reader.is_encrypted
    client.force_login(member.user)
    response = client.get(reverse("proposals:print", args=[member.workspace_id, first.pk]))
    assert response.status_code == 200 and b"<script>" not in response.content
    assert b"&lt;script&gt;" in response.content


def test_new_version_retains_old_content_and_invalidates_approval(proposals):
    first = generate(proposals)
    first = transition(proposals, first, "REVIEWED")
    first = transition(proposals, first, "APPROVED")
    require_approved(first)
    second = generate(proposals)
    first.refresh_from_db()
    assert second.version == 2 and first.version == 1
    assert first.status == "SUPERSEDED" and first.approved_at
    assert first.pdf_sha256 and Proposal.objects.count() == 2
    with pytest.raises(ValidationError):
        require_approved(first)
    with pytest.raises(ValidationError):
        first.save()


def test_unapproved_proposal_cannot_be_marked_sent(proposals):
    proposal = generate(proposals)
    with pytest.raises(ValidationError):
        transition(proposals, proposal, "SENT")
    with pytest.raises(ValidationError):
        transition(proposals, proposal, "APPROVED")
    proposal = transition(proposals, proposal, "REVIEWED")
    with pytest.raises(ValidationError):
        transition(proposals, proposal, "SENT")
    proposal = transition(proposals, proposal, "APPROVED")
    assert transition(proposals, proposal, "SENT").status == "SENT"


def test_approval_roles_stale_revision_and_rollback(proposals):
    member = proposals["member"]
    proposal = generate(proposals)
    reviewed = transition(proposals, proposal, "REVIEWED")
    with pytest.raises(ValidationError):
        transition(proposals, proposal, "APPROVED")
    member.role = "MEMBER"
    member.save()
    with pytest.raises(PermissionDenied):
        transition(proposals, reviewed, "APPROVED")
    member.role = "OWNER"
    member.save()
    with patch("apps.proposals.services.record_event", side_effect=RuntimeError("audit failed")):
        with pytest.raises(RuntimeError):
            transition(proposals, reviewed, "APPROVED")
    reviewed.refresh_from_db()
    assert reviewed.status == "REVIEWED" and reviewed.approved_at is None
    with pytest.raises(IntegrityError), transaction.atomic():
        Proposal.objects.filter(pk=reviewed.pk).update(status="APPROVED")


def test_assets_attach_download_and_archive_without_destroying_history(proposals):
    member = proposals["member"]
    asset = upload(proposals, is_default=True)
    proposal = generate(proposals, assets=[str(asset.pk)])
    assert proposal.attachments.get().asset_id == asset.pk
    with pytest.raises(ValidationError):
        generate(proposals, key=proposal.idempotency_key, assets=[])
    archive_asset(user=member.user, workspace_id=member.workspace_id, pk=asset.pk)
    assert private_document(
        user=member.user, workspace_id=member.workspace_id, pk=asset.pk, asset=True
    )
    with pytest.raises(ValidationError):
        generate(proposals, assets=[str(asset.pk)])
    assert ProposalAttachment.objects.count() == 1


@pytest.mark.parametrize(
    "data",
    [b"not a PDF", b"%PDF-1.7\nbroken", b"a" * 1500001],
    ids=["not_pdf", "broken_pdf", "oversized_pdf"],
)
def test_malformed_and_oversized_assets_are_rejected(proposals, data):
    with pytest.raises(ValidationError):
        upload(proposals, data=data)
    assert not SalesAsset.objects.exists()


def test_active_or_encrypted_pdf_is_rejected():
    from pypdf.generic import DictionaryObject, NameObject, TextStringObject

    for encrypted in (False, True):
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        if encrypted:
            writer.encrypt("secret")
        else:
            writer.root_object[NameObject("/OpenAction")] = DictionaryObject(
                {
                    NameObject("/S"): NameObject("/JavaScript"),
                    NameObject("/JS"): TextStringObject("app.alert('active')"),
                }
            )
        data = io.BytesIO()
        writer.write(data)
        with pytest.raises(ValidationError):
            validate_pdf(data.getvalue())


def test_generation_failure_cleans_up_files_and_leaves_previous_version(proposals, settings):
    first = generate(proposals)
    count = len(list(settings.MEDIA_ROOT.rglob("*.pdf")))
    with patch("apps.proposals.services.record_event", side_effect=RuntimeError("audit failed")):
        with pytest.raises(RuntimeError):
            generate(proposals)
    first.refresh_from_db()
    assert first.status == "GENERATED" and Proposal.objects.count() == 1
    assert len(list(settings.MEDIA_ROOT.rglob("*.pdf"))) == count


def test_job_recovery_uses_original_version_and_mock_is_local(proposals):
    member = proposals["member"]
    job = enqueue_proposal(
        user=member.user,
        workspace_id=member.workspace_id,
        lead_id=proposals["lead"].pk,
        provider="mock",
        idempotency_key="job",
    )
    assert JobRunner().run_once().status == "SUCCEEDED"
    proposal = Proposal.objects.get()
    assert proposal.generated_by == "AI" and proposal.generated_model == "mock-v1"
    assert "IA simulada" in proposal.executive_summary
    Job.objects.filter(pk=job.pk).update(status="PENDING")
    with patch(
        "apps.proposals.services.document_pdf", side_effect=AssertionError("do not rerender")
    ):
        assert JobRunner().run_once().status == "SUCCEEDED"
    assert Proposal.objects.count() == 1


def test_tampered_paths_and_hashes_cannot_be_downloaded(proposals):
    member = proposals["member"]
    proposal = generate(proposals)
    Proposal.objects.filter(pk=proposal.pk).update(pdf="../.env")
    with pytest.raises(ValidationError):
        private_document(user=member.user, workspace_id=member.workspace_id, pk=proposal.pk)


def test_cross_workspace_assets_and_proposals_are_hidden(proposals):
    member, foreign = proposals["member"], proposals["foreign"]
    asset = upload(proposals)
    proposal = generate(proposals)
    with pytest.raises(Http404):
        private_document(user=foreign.user, workspace_id=member.workspace_id, pk=proposal.pk)
    with pytest.raises(Http404):
        private_document(
            user=foreign.user, workspace_id=foreign.workspace_id, pk=asset.pk, asset=True
        )
    with pytest.raises(Http404):
        generate_proposal(
            user=foreign.user,
            workspace_id=foreign.workspace_id,
            payload=payload(proposals),
            idempotency_key="foreign",
        )


def test_invalid_generation_payloads():
    for data in (
        {},
        {"lead_id": "bad", "provider": "template", "asset_ids": []},
        {"lead_id": str(uuid.uuid4()), "provider": "live", "asset_ids": []},
    ):
        with pytest.raises(ValidationError):
            validate_payload(Job.Kind.GENERATE_PROPOSAL, data)


def test_ui_generation_review_approval_csrf_and_download(proposals):
    member = proposals["member"]
    client = Client(enforce_csrf_checks=True)
    url = reverse("proposals:generate", args=[member.workspace_id])
    client.force_login(member.user)
    assert client.get(url).status_code == 200
    data = {"lead": proposals["lead"].pk, "provider": "template", "idempotency_key": uuid.uuid4()}
    assert client.post(url, data).status_code == 403
    token = client.cookies["csrftoken"].value
    data["csrfmiddlewaretoken"] = token
    assert client.post(url, data).status_code == 302
    assert JobRunner().run_once().status == "SUCCEEDED"
    proposal = Proposal.objects.get()
    detail = reverse("proposals:detail", args=[member.workspace_id, proposal.pk])
    assert client.get(detail).status_code == 200
    for target, revision in (("REVIEWED", 0), ("APPROVED", 1)):
        assert (
            client.post(
                detail,
                {"action": target, "expected_revision": revision, "csrfmiddlewaretoken": token},
            ).status_code
            == 302
        )
    pdf = client.get(reverse("proposals:download", args=[member.workspace_id, proposal.pk]))
    assert pdf.status_code == 200 and pdf["Cache-Control"] == "private, no-store"
    assert b"".join(pdf.streaming_content).startswith(b"%PDF-")
    assert client.get(reverse("proposals:list", args=[member.workspace_id])).status_code == 200
    assert client.get(reverse("proposals:assets", args=[member.workspace_id])).status_code == 200
    client.force_login(proposals["foreign"].user)
    assert client.get(detail).status_code == 404
    member.role = "VIEWER"
    member.save()
    client.force_login(member.user)
    assert client.get(url).status_code == 403


@pytest.mark.django_db(transaction=True)
def test_pdf_rendering_is_outside_transaction_and_revocation_prevents_persistence(proposals):
    from django.db import connection

    from apps.proposals.documents import document_pdf

    member = proposals["member"]

    def revoke(proposal, snapshot):
        assert not connection.in_atomic_block
        result = document_pdf(proposal, snapshot)
        member.is_active = False
        member.save()
        return result

    with patch("apps.proposals.services.document_pdf", side_effect=revoke):
        with pytest.raises(Http404):
            generate(proposals)
    assert not Proposal.objects.exists()


def test_approval_refuses_tampered_pdf(proposals):
    member = proposals["member"]
    proposal = transition(proposals, generate(proposals), "REVIEWED")
    with proposal.pdf.storage.open(proposal.pdf.name, "wb") as handle:
        handle.write(b"tampered")
    with pytest.raises(ValidationError):
        private_document(user=member.user, workspace_id=member.workspace_id, pk=proposal.pk)
    with pytest.raises(ValidationError):
        transition(proposals, proposal, "APPROVED")
    proposal.refresh_from_db()
    assert proposal.status == "REVIEWED"


def test_missing_agency_services_and_archived_lead_are_controlled(proposals):
    from apps.accounts.models import AgencyProfile

    member = proposals["member"]
    proposals["lead"].is_archived = True
    proposals["lead"].save()
    with pytest.raises(ValidationError):
        generate(proposals)
    proposals["lead"].is_archived = False
    proposals["lead"].save()
    AgencyProfile.objects.filter(workspace=member.workspace).delete()
    with pytest.raises(ValidationError):
        generate(proposals)
    AgencyProfileFactory(workspace=member.workspace)
    CampaignService.objects.filter(campaign=proposals["campaign"]).delete()
    with pytest.raises(ValidationError):
        generate(proposals)


def test_asset_upload_failure_compensates_storage(proposals, settings):
    with patch("apps.proposals.services.record_event", side_effect=RuntimeError("audit failed")):
        with pytest.raises(RuntimeError):
            upload(proposals)
    assert not SalesAsset.objects.exists()
    assert not list(settings.MEDIA_ROOT.rglob("*.pdf"))


def test_streaming_upload_limit_does_not_affect_csv_routes(proposals, client):
    from django.core.files.uploadhandler import StopUpload
    from django.test import RequestFactory

    from apps.proposals.uploads import BoundedAssetUploadHandler

    member = proposals["member"]
    client.force_login(member.user)
    url = reverse("proposals:assets", args=[member.workspace_id])
    response = client.post(
        url,
        {
            "name": "Too large",
            "asset_type": "BROCHURE",
            "file": SimpleUploadedFile(
                "large.pdf", b"%PDF-1.7\n" + b"x" * 1_500_001, content_type="application/pdf"
            ),
        },
    )
    assert response.status_code == 400 and not SalesAsset.objects.exists()
    handler = BoundedAssetUploadHandler(RequestFactory().post("/w/demo/prospecting/import/"))
    assert handler.receive_data_chunk(b"x" * 1_500_001, 0)
    handler = BoundedAssetUploadHandler(RequestFactory().post(url))
    handler.new_file("file", "file.pdf", "application/pdf", 10)
    with pytest.raises(StopUpload):
        handler.new_file("file2", "file2.pdf", "application/pdf", 10)


def test_asset_ui_upload_default_selection_and_archive(proposals, client):
    member = proposals["member"]
    client.force_login(member.user)
    url = reverse("proposals:assets", args=[member.workspace_id])
    assert (
        client.post(
            url,
            {
                "name": "Demo",
                "asset_type": "BROCHURE",
                "is_default": "on",
                "file": SimpleUploadedFile(
                    "asset.pdf", sample_pdf(), content_type="application/pdf"
                ),
            },
        ).status_code
        == 302
    )
    asset = SalesAsset.objects.get()
    job = enqueue_proposal(
        user=member.user,
        workspace_id=member.workspace_id,
        lead_id=proposals["lead"].pk,
        idempotency_key="defaults",
    )
    assert job.payload["asset_ids"] == [str(asset.pk)]
    assert JobRunner().run_once().status == "SUCCEEDED"
    archive = reverse("proposals:archive_asset", args=[member.workspace_id, asset.pk])
    assert client.get(archive).status_code == 405
    assert client.post(archive).status_code == 302
    assert (
        client.get(
            reverse("proposals:asset_download", args=[member.workspace_id, asset.pk])
        ).status_code
        == 200
    )
