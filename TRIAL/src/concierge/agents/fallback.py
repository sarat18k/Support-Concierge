"""Heuristic stand-ins when OPENAI_API_KEY is missing.

Not a production substitute for gpt-4o-mini. Keeps the pipeline completable
and policy-safe so a missing key cannot drop tickets.
"""

from __future__ import annotations

from concierge.models import Classification, DraftResult, Extraction, Ticket
from concierge.signals import MONEY_LOOSE, scan


def heuristic_classify(ticket: Ticket) -> Classification:
    sig = scan(ticket)
    multi = sig.multi_issue
    if sig.injection or sig.refund:
        category, conf, why = "billing", 0.88, "Refund language in ticket."
        if sig.injection:
            why = "Refund request plus instruction-override attempt."
    elif sig.gdpr or sig.deletion:
        category, conf, why = "legal_privacy", 0.93, "Regulatory / deletion request."
    elif sig.security:
        category, conf, why = "security", 0.92, "Possible tenant isolation / security report."
    elif sig.spammy:
        category, conf, why = "spam", 0.95, "Unsolicited promotional outreach."
    elif sig.gratitude:
        category, conf, why = "gratitude", 0.94, "Thanks, no action requested."
    elif sig.feature_request:
        category, conf, why = "feature_request", 0.91, "Product enhancement ask."
    elif "overcharg" in sig.low or "cancelling" in sig.low:
        category, conf, why = "billing", 0.9, "Billing dispute with cancellation threat."
        multi = True
    elif sig.money or "charged twice" in sig.low:
        category, conf, why = "billing", 0.87, "Billing / pricing question."
        if "charged twice" in sig.low:
            multi = True
    elif sig.account_access:
        category, conf, why = "account_access", 0.9, "Cannot access account."
    elif sig.bug:
        category, conf, why = "bug", 0.88, "Product malfunction."
        if "charged" in sig.low:
            multi = True
    elif is_low_signal(ticket):
        category, conf, why = "unclear", 0.35, "Too little information."
    else:
        category, conf, why = "unclear", 0.4, "No strong keyword match."

    return Classification(
        category=category,
        language="es" if sig.spanish else "en",
        multi_intent=multi,
        prompt_injection=sig.injection,
        confidence=conf,
        rationale=f"[heuristic] {why}",
    )


def is_low_signal(ticket: Ticket) -> bool:
    return ticket.body.strip().lower() in {"help"} or len(ticket.body.split()) <= 2


def heuristic_extract(ticket: Ticket) -> Extraction:
    sig = scan(ticket)
    secondary: list[str] = []
    if sig.multi_issue:
        secondary.append("billing_double_charge" if "charged" in sig.low else "second_issue")

    product = None
    if "export" in sig.low or "csv" in sig.low or "reports" in sig.low:
        product = "reports"
    elif "dashboard" in sig.low:
        product = "dashboard"
    elif "dark mode" in sig.low:
        product = "ui"
    elif "password" in sig.low or "sesión" in sig.low:
        product = "auth"

    if "frustrated" in sig.low or "third time" in sig.low:
        sentiment = "angry"
    elif "thank" in sig.low:
        sentiment = "positive"
    elif "not working" in sig.low or "broken" in sig.low:
        sentiment = "negative"
    else:
        sentiment = "neutral"

    if sig.security or sig.gdpr:
        urgency = "critical"
    elif "cancelling" in sig.low or sig.refund or "can't log" in sig.low:
        urgency = "high"
    else:
        urgency = "medium"

    return Extraction(
        product_area=product,
        urgency=urgency,
        sentiment=sentiment,
        identifiers=list(sig.identifiers),
        is_followup=sig.followup,
        secondary_intents=secondary,
        money_mentioned=bool(MONEY_LOOSE.search(sig.low)),
        refund_requested=sig.refund,
        cancellation_threatened=sig.cancel,
        deletion_requested=sig.deletion or sig.gdpr,
        legal_language=sig.gdpr,
        security_report=sig.security,
        confidence=0.86,
        rationale="[heuristic] keyword extraction",
    )


def heuristic_draft(ticket: Ticket, language: str = "en") -> DraftResult:
    name = ticket.from_name.split()[0] if ticket.from_name else "there"
    if language.startswith("es"):
        body = (
            f"Hola {name},\n\n"
            "Gracias por escribirnos. Hemos recibido tu mensaje y un especialista "
            "de soporte lo revisará. No podemos confirmar cambios en facturación, "
            "accesos o plazos hasta esa revisión.\n\n"
            "Un saludo,\nCorvus Support"
        )
    else:
        body = (
            f"Hi {name},\n\n"
            "Thanks for writing in. We've received your request and a specialist "
            "will review it shortly. We don't have account-specific details in this "
            "first-line pass, so we won't confirm billing changes, access restores, "
            "or timelines until a human has looked.\n\n"
            "Best,\nCorvus Support"
        )
    return DraftResult(
        body=body,
        template_used="offline_generic",
        language=language,
        confidence=0.55,
        rationale="Offline/heuristic draft pending human review.",
    )
