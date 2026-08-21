"""Deterministic policy gate. Never auto-resolve money, legal, deletion, or security."""

from __future__ import annotations

from concierge.models import Category, Classification, Extraction, PolicyResult, Ticket
from concierge.signals import scan

AUTO_OK = {Category.SPAM, Category.GRATITUDE, Category.FEATURE_REQUEST}


def apply_policy(
    ticket: Ticket,
    classification: Classification | None,
    extraction: Extraction | None,
) -> PolicyResult:
    """Union of raw-text scans and model flags. Model cannot waive a hard rule."""
    sig = scan(ticket)
    flags: list[str] = []

    refund = sig.refund or bool(extraction and extraction.refund_requested)
    money = bool(
        refund
        or sig.money
        or (extraction and extraction.money_mentioned)
        or (classification and classification.category == Category.BILLING)
    )
    deletion = bool(
        sig.deletion
        or sig.gdpr
        or (extraction and extraction.deletion_requested)
        or (classification and classification.category == Category.LEGAL_PRIVACY)
    )
    security = bool(
        sig.security
        or (extraction and extraction.security_report)
        or (classification and classification.category == Category.SECURITY)
    )
    legal = bool(
        sig.gdpr
        or (extraction and extraction.legal_language)
        or (classification and classification.category == Category.LEGAL_PRIVACY)
    )
    injection = sig.injection or bool(classification and classification.prompt_injection)
    cancel = bool(
        sig.cancel
        or (extraction and extraction.cancellation_threatened)
        or (classification and classification.category == Category.CANCELLATION)
    )
    churn = sig.churn or bool(classification and classification.category == Category.CHURN_RISK)
    multi = bool(
        sig.multi_issue
        or (classification and classification.multi_intent)
        or (extraction and extraction.secondary_intents)
    )

    if refund:
        flags.append("refund_requested")
    if money:
        flags.append("money_involved")
    if deletion:
        flags.append("account_deletion")
    if security:
        flags.append("security_report")
    if legal:
        flags.append("legal_or_regulatory")
    if injection:
        flags.append("prompt_injection")
    if cancel:
        flags.append("cancellation_threat")
    if churn:
        flags.append("churn_risk")
    if multi and money:
        flags.append("money_plus_multi_intent")
    if sig.ultimatum:
        flags.append("same_day_ultimatum")

    force_escalate = bool(
        refund
        or deletion
        or security
        or legal
        or injection
        or (cancel and money)
        or (multi and money)
        or sig.ultimatum
    )

    category = classification.category if classification else Category.UNCLEAR
    allow_auto = category in AUTO_OK and not money and not force_escalate
    if allow_auto:
        flags = [f for f in flags if f != "churn_risk"]

    if force_escalate:
        rationale = "Hard policy requires a human: " + ", ".join(flags) + "."
    elif not allow_auto:
        rationale = (
            "Auto-resolve is forbidden (human must stay in the loop): "
            + (", ".join(flags) or category.value)
            + "."
        )
    else:
        rationale = (
            f"Category {category.value} is eligible for auto-resolve; "
            "no money/legal/security flags."
        )

    return PolicyResult(
        allow_auto_resolve=allow_auto,
        force_escalate=force_escalate,
        flags=flags,
        rationale=rationale,
    )
