"""Decision agent: policy + composite confidence → routing action.

This agent is deterministic on purpose. The client's hard rule must not
depend on an LLM 'feeling' confident about a refund.
"""

from concierge.confidence import AUTO_RESOLVE_MIN, DRAFT_MIN, score
from concierge.models import (
    Action,
    Classification,
    DecisionResult,
    Extraction,
    PolicyResult,
    Ticket,
)

AUTO_CATEGORIES = {"spam", "gratitude", "feature_request"}


def decide(
    ticket: Ticket,
    classification: Classification | None,
    extraction: Extraction | None,
    policy: PolicyResult,
    *,
    failed: bool = False,
) -> DecisionResult:
    breakdown = score(ticket, classification, extraction)
    thresholds = {
        "auto_resolve_min": AUTO_RESOLVE_MIN,
        "draft_min": DRAFT_MIN,
    }
    category = classification.category.value if classification else "unclear"

    if failed:
        action = Action.ESCALATE
        rationale = (
            "An upstream agent failed; degrading safely to escalate so a human sees the ticket."
        )
    elif policy.force_escalate:
        action = Action.ESCALATE
        rationale = policy.rationale
    elif breakdown.garbled or breakdown.vague or breakdown.composite < DRAFT_MIN:
        action = Action.ESCALATE
        rationale = (
            f"Composite confidence {breakdown.composite:.2f} is below draft threshold "
            f"{DRAFT_MIN:.2f}"
            + (", text is vague" if breakdown.vague else "")
            + (", text is garbled" if breakdown.garbled else "")
            + ". Escalating rather than guessing."
        )
    elif (
        policy.allow_auto_resolve
        and category in AUTO_CATEGORIES
        and breakdown.composite >= AUTO_RESOLVE_MIN
    ):
        action = Action.AUTO_RESOLVE
        rationale = (
            f"Category {category} is auto-eligible, policy allows auto-resolve, "
            f"and composite confidence {breakdown.composite:.2f} >= {AUTO_RESOLVE_MIN:.2f}."
        )
    else:
        action = Action.DRAFT_FOR_REVIEW
        rationale = (
            f"Human stays in the loop. Category={category}, composite="
            f"{breakdown.composite:.2f}, policy_flags={policy.flags or ['none']}. "
            f"{policy.rationale}"
        )

    return DecisionResult(
        action=action,
        composite_confidence=breakdown.composite,
        model_confidence=breakdown.model_confidence,
        heuristic_deltas=breakdown.heuristic_deltas,
        thresholds=thresholds,
        policy_flags=policy.flags,
        rationale=rationale,
    )
