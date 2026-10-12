from django.template.loader import render_to_string

from .contracts import GeneratedProposal, ProposalContext


def template_proposal(context: ProposalContext) -> GeneratedProposal:
    """Render deterministic plain text from a frozen, bounded commercial context."""
    data = context.snapshot
    return GeneratedProposal(
        title=f"Propuesta para {data['company']['name']}",
        executive_summary=render_to_string("proposals/text/summary.txt", data).strip(),
        problem_statement=render_to_string("proposals/text/problem.txt", data).strip(),
        recommended_solution=render_to_string("proposals/text/solution.txt", data).strip(),
        next_steps=render_to_string("proposals/text/next_steps.txt", data).strip(),
    )


class MockAIProvider:
    def generate_proposal(self, context: ProposalContext) -> GeneratedProposal:
        """Produce labelled fixture text locally, never call an AI endpoint."""
        generated = template_proposal(context)
        return GeneratedProposal(
            **{
                **generated.__dict__,
                "executive_summary": "Demostración de IA simulada. " + generated.executive_summary,
            }
        )
