from django.core.exceptions import ValidationError

from .models import Proposal


def require_approved(proposal: Proposal) -> None:
    """Approval of this immutable version is necessary for any future send operation."""
    if (
        proposal.status != Proposal.Status.APPROVED
        or not proposal.approved_at
        or not proposal.approved_by_id
    ):
        raise ValidationError("Solo una propuesta APPROVED es elegible para envío.")
