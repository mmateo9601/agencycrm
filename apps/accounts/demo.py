"""Explicit, offline creation of the playbook's fictional demonstration workspace."""

from dataclasses import dataclass
from uuid import UUID, uuid5

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.campaigns.services import change_campaign_status, save_campaign, save_channel
from apps.companies.services import save_company, save_contact, save_external_ref
from apps.compliance.services import add_suppression
from apps.crm.services import LeadStateService, save_lead, save_opportunity
from apps.jobs.locking import WorkerLock
from apps.jobs.models import Job
from apps.jobs.services import JobService
from apps.outreach.services import prepare_message
from apps.proposals.services import generate_proposal
from apps.scoring.services import calculate_score

from .models import AgencyProfile, Membership, ServiceOffering, User, Workspace

DEMO_NAMESPACE = UUID("9e841a02-bf62-41b4-9d9f-8da6c50f4291")
DEMO_WORKSPACE_ID = uuid5(DEMO_NAMESPACE, "workspace-v1")
DEMO_ACTOR_ID = uuid5(DEMO_NAMESPACE, "actor-v1")
DEMO_ACTOR_NAME = "_agencycrm_demo_seed"
DEMO_SLUG = "demo-agency"
DEMO_ACTION = "DEMO_SEEDED_V1"
NAMES = (
    "Clínica Norte Demo",
    "Clínica Atlántico Demo",
    "Dental Demo",
    "Clínica Ría Demo",
    "Clínica Faro Demo",
    "Clínica Brisa Demo",
    "Clínica Mar Demo",
    "Clínica Bosque Demo",
    "Clínica Monte Demo",
    "Clínica Valle Demo",
)
PATHS = (
    (),
    ("ENRICHING",),
    ("QUALIFIED",),
    ("QUALIFIED", "READY"),
    ("QUALIFIED", "READY", "CONTACTED"),
    ("QUALIFIED", "READY", "CONTACTED", "REPLIED"),
    ("QUALIFIED", "READY", "CONTACTED", "MEETING"),
    ("QUALIFIED", "READY", "CONTACTED", "REPLIED", "PROPOSAL"),
    ("QUALIFIED", "READY", "CONTACTED", "MEETING", "WON"),
    ("LOST",),
)


@dataclass(frozen=True)
class SeedResult:
    workspace_id: UUID
    created: bool
    user_created: bool


def workspace_event(*, workspace: Workspace, actor: User, action: str, metadata: dict) -> None:
    """Audit only explicit seed identifiers; never account credentials."""
    AuditEvent.objects.create(
        workspace=workspace,
        actor=actor,
        action=action,
        object_type=Workspace._meta.label,
        object_id=workspace.pk,
        metadata=metadata,
    )


def validate_identity(workspace: Workspace | None) -> None:
    """Reject reserved identifier collisions rather than adopting operator records."""
    if workspace and (
        workspace.pk != DEMO_WORKSPACE_ID
        or workspace.slug != DEMO_SLUG
        or not workspace.is_active
        or not AuditEvent.objects.filter(
            workspace=workspace, action=DEMO_ACTION, object_id=workspace.pk
        ).exists()
    ):
        raise ValidationError("El identificador demo ya está ocupado o el workspace está inactivo.")
    if not workspace and (
        Workspace.objects.filter(slug=DEMO_SLUG).exists()
        or User.objects.filter(pk=DEMO_ACTOR_ID).exists()
        or User.objects.filter(username=DEMO_ACTOR_NAME).exists()
    ):
        raise ValidationError("Los identificadores reservados del seed ya están ocupados.")


def demo_actor(workspace: Workspace) -> User:
    """Require the original non-login actor and its unchanged active OWNER membership."""
    actor = User.objects.filter(pk=DEMO_ACTOR_ID, username=DEMO_ACTOR_NAME).first()
    if (
        actor is None
        or not actor.is_active
        or actor.is_staff
        or actor.is_superuser
        or actor.has_usable_password()
        or not Membership.objects.filter(
            workspace=workspace, user=actor, role="OWNER", is_active=True
        ).exists()
    ):
        raise ValidationError("La cuenta técnica demo ha cambiado; no se restablecen sus permisos.")
    return actor


def add_demo_user(*, workspace: Workspace, actor: User, username: str | None) -> bool:
    """Create an optional login account without a password or privilege escalation on reuse."""
    if username is None:
        return False
    if not isinstance(username, str) or username != username.strip() or not username:
        raise ValidationError("Indica un nombre de usuario válido sin espacios exteriores.")
    username = User.normalize_username(username)
    candidate = User(username=username)
    candidate.full_clean(exclude=["password"], validate_unique=False, validate_constraints=False)
    # Validation of an existing username is handled below, without resetting its account.
    existing = User.objects.filter(username=username).first()
    if existing:
        if not AuditEvent.objects.filter(
            workspace=workspace, action="DEMO_USER_CREATED", metadata__user_id=str(existing.pk)
        ).exists():
            raise ValidationError("El usuario ya existe y no fue creado por este seed.")
        return False
    user = User.objects.create_user(username=username, password=None)
    Membership.objects.create(workspace=workspace, user=user, role="OWNER")
    workspace_event(
        workspace=workspace,
        actor=actor,
        action="DEMO_USER_CREATED",
        metadata={"user_id": str(user.pk)},
    )
    return True


def create_demo_data(*, workspace: Workspace, actor: User, files: list[str]) -> None:
    """Use ordinary application services; never call providers, approve or enqueue sends."""
    AgencyProfile.objects.create(
        workspace=workspace,
        trading_name="Demo Agency",
        legal_name="Agencia ficticia",
        website="https://agency.example.invalid/",
        email="agency@example.invalid",
        value_proposition="Ejemplo ficticio de captación local y publicidad.",
        default_sender_name="Equipo Demo",
        proposal_footer="Documento ficticio de demostración.",
    )
    services = [
        ServiceOffering.objects.create(
            workspace=workspace, code=code, name=name, default_pitch=pitch, sort_order=order
        )
        for order, (code, name, pitch) in enumerate(
            (
                ("SEO_LOCAL", "SEO Local", "Mejorar la visibilidad local de forma medible."),
                ("GOOGLE_ADS", "Google Ads", "Planificar campañas con objetivos verificables."),
            )
        )
    ]
    campaign = save_campaign(
        user=actor,
        workspace_id=workspace.pk,
        data={
            "name": "Dentistas Galicia",
            "description": "Campaña ficticia de demostración.",
            "target_query": "dentistas",
            "target_sector": "Dentistas",
            "target_location_text": "A Coruña",
            "desired_leads": 10,
            "services": services,
        },
    )
    change_campaign_status(user=actor, workspace_id=workspace.pk, pk=campaign.pk, target="READY")
    for channel in ("EMAIL", "WHATSAPP", "PHONE"):
        save_channel(
            user=actor,
            workspace_id=workspace.pk,
            data={"campaign": campaign, "channel": channel, "enabled": True},
        )
    leads, contacts = [], []
    for index, (name, path) in enumerate(zip(NAMES, PATHS, strict=True), start=1):
        company = save_company(
            user=actor,
            workspace_id=workspace.pk,
            data={
                "name": name,
                "website": f"https://dental{index}.example.invalid/",
                "city": "A Coruña",
                "primary_sector": "Dentistas",
                "description": "Empresa ficticia; no corresponde a un negocio real.",
            },
        )
        save_external_ref(
            user=actor,
            workspace_id=workspace.pk,
            data={
                "company": company,
                "provider": "SEED_DEMO",
                "external_id": f"dental-{index}",
                "metadata": {"fictional": True, "seed_version": 1},
            },
        )
        contact = save_contact(
            user=actor,
            workspace_id=workspace.pk,
            data={
                "company": company,
                "full_name": f"Contacto ficticio {index}",
                "email": f"dental{index}@example.invalid",
                "is_primary": True,
                "notes": "Contacto ficticio. No se asignan teléfonos reales.",
            },
        )
        lead = save_lead(
            user=actor,
            workspace_id=workspace.pk,
            data={
                "company": company,
                "campaign": campaign,
                "recommended_service": services[0],
                "notes": "Historial comercial ficticio creado por seed_demo.",
            },
        )
        save_opportunity(
            user=actor,
            workspace_id=workspace.pk,
            data={
                "lead": lead,
                "type": "SEO_LOCAL",
                "severity": "MEDIUM",
                "title": "Ejemplo de oportunidad local",
                "recommended_service": services[0],
                "evidence": "Evidencia ficticia para la demo; no procede de una web real.",
            },
        )
        calculate_score(
            user=actor,
            workspace_id=workspace.pk,
            lead_id=lead.pk,
            idempotency_key=f"seed-demo-v1:score:{index}",
        )
        for target in path:
            lead = LeadStateService.transition(
                user=actor,
                workspace_id=workspace.pk,
                pk=lead.pk,
                target=target,
                reason="Estado histórico ficticio de demostración.",
            )
        leads.append(lead)
        contacts.append(contact)
    proposal = generate_proposal(
        user=actor,
        workspace_id=workspace.pk,
        payload={"lead_id": str(leads[3].pk), "provider": "template", "asset_ids": []},
        idempotency_key="seed-demo-v1:proposal",
    )
    files.append(proposal.pdf.name)
    for channel in ("EMAIL", "WHATSAPP"):
        prepare_message(
            user=actor,
            workspace_id=workspace.pk,
            proposal_id=proposal.pk,
            contact_id=contacts[3].pk,
            channel=channel,
            idempotency_key=f"seed-demo-v1:message:{channel}",
            body_text="Mensaje ficticio pendiente de revisión. No se envía desde el seed.",
        )
    add_suppression(
        user=actor,
        workspace_id=workspace.pk,
        scope="COMPANY",
        value="",
        company_id=leads[-1].company_id,
        reason="MANUAL_BLOCK",
        source="Demo ficticia",
    )
    # Actual terminal history, without fabricated receipts or claiming another workspace's jobs.
    for index in range(2):
        job = JobService.enqueue(
            user=actor,
            workspace_id=workspace.pk,
            kind=Job.Kind.CHECK_MOCK_PROVIDERS,
            payload={"scenario": "success"},
            idempotency_key=f"seed-demo-v1:job:{index}",
        )
        JobService.cancel(user=actor, workspace_id=workspace.pk, pk=job.pk)


def seed_demo(*, username: str | None = None) -> SeedResult:
    """Create the demo once, under the worker lock, compensating PDF storage on rollback.

    A completed seed is never replayed or repaired implicitly. Operator edits,
    approvals, credentials and memberships remain unchanged on subsequent calls.
    """
    if settings.OUTREACH_MODE != "simulation":
        raise ValidationError("seed_demo requiere OUTREACH_MODE=simulation.")
    files: list[str] = []
    try:
        with WorkerLock(), transaction.atomic():
            workspace = Workspace.objects.filter(pk=DEMO_WORKSPACE_ID).first()
            validate_identity(workspace)
            created = workspace is None
            if created:
                workspace = Workspace.objects.create(
                    id=DEMO_WORKSPACE_ID, name="Demo Agency", slug=DEMO_SLUG
                )
                actor = User.objects.create_user(
                    id=DEMO_ACTOR_ID, username=DEMO_ACTOR_NAME, password=None
                )
                Membership.objects.create(workspace=workspace, user=actor, role="OWNER")
            else:
                actor = demo_actor(workspace)
            user_created = add_demo_user(workspace=workspace, actor=actor, username=username)
            if created:
                create_demo_data(workspace=workspace, actor=actor, files=files)
                workspace_event(
                    workspace=workspace,
                    actor=actor,
                    action=DEMO_ACTION,
                    metadata={"seed_version": 1, "fictional": True, "companies": 10},
                )
            return SeedResult(workspace.pk, created, user_created)
    except Exception:
        # The only files tracked here were newly generated by this invocation.
        for name in files:
            default_storage.delete(name)
        raise
