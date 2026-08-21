"""Composite confidence: model score plus heuristic modifiers. Thresholds live here."""

from __future__ import annotations

from concierge.models import Classification, ConfidenceBreakdown, Extraction, Ticket
from concierge.signals import is_vague, scan

AUTO_RESOLVE_MIN = 0.85
DRAFT_MIN = 0.55

DELTAS = {
    "vague": -0.35,
    "garbled": -0.25,
    "multi_intent": -0.15,
    "injection": -0.40,
    "unclear": -0.20,
    "followup": -0.05,
    "clear_single_intent": 0.05,
}


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def model_confidence(
    classification: Classification | None,
    extraction: Extraction | None,
) -> float:
    scores = []
    if classification is not None:
        scores.append(classification.confidence)
    if extraction is not None:
        scores.append(extraction.confidence)
    return sum(scores) / len(scores) if scores else 0.2


def score(
    ticket: Ticket,
    classification: Classification | None,
    extraction: Extraction | None,
) -> ConfidenceBreakdown:
    sig = scan(ticket)
    base = model_confidence(classification, extraction)
    deltas: dict[str, float] = {}
    vague = is_vague(ticket)

    if vague:
        deltas["vague"] = DELTAS["vague"]
    if sig.garbled:
        deltas["garbled"] = DELTAS["garbled"]
    if (classification and classification.multi_intent) or (
        extraction and extraction.secondary_intents
    ):
        deltas["multi_intent"] = DELTAS["multi_intent"]
    if (classification and classification.prompt_injection) or sig.injection:
        deltas["injection"] = DELTAS["injection"]
    if classification and classification.category.value == "unclear":
        deltas["unclear"] = DELTAS["unclear"]
    if (extraction and extraction.is_followup) or sig.followup:
        deltas["followup"] = DELTAS["followup"]
    if (
        classification
        and not classification.multi_intent
        and not vague
        and not sig.garbled
        and classification.confidence >= 0.8
        and classification.category.value != "unclear"
    ):
        deltas["clear_single_intent"] = DELTAS["clear_single_intent"]

    return ConfidenceBreakdown(
        model_confidence=round(base, 4),
        heuristic_deltas={k: round(v, 4) for k, v in deltas.items()},
        composite=round(_clamp(base + sum(deltas.values())), 4),
        vague=vague,
        garbled=sig.garbled,
    )
