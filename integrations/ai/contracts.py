from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ProposalContext:
    snapshot: dict


@dataclass(frozen=True)
class GeneratedProposal:
    title: str
    executive_summary: str
    problem_statement: str
    recommended_solution: str
    next_steps: str


class AIProvider(Protocol):
    def generate_proposal(self, context: ProposalContext) -> GeneratedProposal:
        """Return text only; providers never persist, approve or send proposals."""
        ...
