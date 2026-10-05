from .models import Lead

TRANSITIONS = {
    "NEW": {"ENRICHING", "QUALIFIED", "DISQUALIFIED", "LOST"},
    "ENRICHING": {"QUALIFIED", "DISQUALIFIED", "LOST"},
    "QUALIFIED": {"READY", "DISQUALIFIED", "LOST"},
    "READY": {"CONTACTED", "DISQUALIFIED", "LOST"},
    "CONTACTED": {"REPLIED", "MEETING", "LOST"},
    "REPLIED": {"MEETING", "PROPOSAL", "LOST"},
    "MEETING": {"PROPOSAL", "WON", "LOST"},
    "PROPOSAL": {"WON", "LOST"},
    "WON": set(),
    "LOST": set(),
    "DISQUALIFIED": set(),
}


def allowed_transitions(lead: Lead) -> list[tuple[str, str]]:
    """Only show transitions applicable to an active lead and its current state."""
    if lead.is_archived or not lead.company.is_active or lead.campaign.status == "ARCHIVED":
        return []
    return [
        (value, label) for value, label in Lead.Status.choices if value in TRANSITIONS[lead.status]
    ]
