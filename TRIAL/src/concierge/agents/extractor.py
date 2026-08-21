"""Extractor agent: entities, urgency, sentiment, risk flags."""

from concierge.agents.fallback import heuristic_extract
from concierge.llm import format_ticket_block, invoke_structured, llm_available
from concierge.models import Extraction, Ticket

SYSTEM = """Extract structured fields from a SaaS support ticket.

Set booleans conservatively (true only if clearly present):
- money_mentioned: invoices, charges, pricing, payments
- refund_requested: they want money back
- cancellation_threatened: they will cancel or tell others to leave if not fixed
- deletion_requested: delete account and/or personal data
- legal_language: GDPR, statute, 'required by law', counsel
- security_report: possible vuln, IDOR, seeing another tenant's data
- is_followup: they say they already reported this

secondary_intents: extra asks beyond the primary one, short labels.
product_area: e.g. reports, dashboard, auth, billing, ui.
urgency: low/medium/high/critical.
sentiment: positive/neutral/negative/angry.
identifiers: account ids, plan names, dollar amounts, browsers as strings.
confidence: how complete/reliable this extraction is, 0-1.
"""


def extract(ticket: Ticket) -> Extraction:
    if not llm_available():
        return heuristic_extract(ticket)
    user = format_ticket_block(
        ticket.subject, ticket.body, ticket.from_name, ticket.from_email
    )
    return invoke_structured(Extraction, SYSTEM, user, "extractor")
