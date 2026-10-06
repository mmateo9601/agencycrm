from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from integrations.common.contracts import PlaceTextSearchRequest, SendMessageRequest
from integrations.common.factory import outbound_provider, places_provider

from .models import Job


@dataclass(frozen=True)
class ExecutionContext:
    job_id: UUID
    workspace_id: UUID
    attempt: int
    payload: dict
    progress: Callable[[int, int], None]


def check_mock_providers(context: ExecutionContext) -> dict:
    """Exercise adapters with synthetic fixtures, never campaign or contact data."""
    scenario = context.payload["scenario"]
    failure = (
        "permanent"
        if scenario == "permanent"
        else "temporary"
        if scenario == "temporary_once" and context.attempt == 1
        else ""
    )
    context.progress(0, 3)
    page = places_provider(provider="mock", failure=failure).text_search(
        PlaceTextSearchRequest(query="Diagnóstico ficticio")
    )
    context.progress(1, 3)
    request = SendMessageRequest(
        workspace_id=context.workspace_id,
        idempotency_key=str(context.job_id),
        recipient="diagnostic@example.invalid",
        body_text="Prueba ficticia de transporte.",
    )
    email = outbound_provider("EMAIL").send(request)
    context.progress(2, 3)
    whatsapp = outbound_provider("WHATSAPP").send(request)
    context.progress(3, 3)
    return {
        "simulated": True,
        "places_count": len(page.places),
        "email_receipt": email.provider_message_id,
        "whatsapp_receipt": whatsapp.provider_message_id,
    }


def run_search_item(context: ExecutionContext) -> dict:
    """Dispatch prospection without coupling provider contracts to the ORM."""
    from apps.prospecting.execution import run_search

    return run_search(context)


HANDLERS: dict[str, Callable[[ExecutionContext], dict]] = {
    Job.Kind.CHECK_MOCK_PROVIDERS: check_mock_providers,
    Job.Kind.RUN_SEARCH_ITEM: run_search_item,
}
