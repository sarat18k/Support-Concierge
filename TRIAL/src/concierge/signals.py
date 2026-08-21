"""Single pass over ticket text. Policy, confidence, and fallback all share this."""

from __future__ import annotations

import re
from dataclasses import dataclass

from concierge.models import Ticket

REFUND = re.compile(r"\brefunds?\b|\breimburse\b", re.I)
OVERCHARGE = re.compile(r"overcharg|charged twice|double.?charg|charged me", re.I)
INVOICE = re.compile(r"\binvoice\b|\bbilling\b|\bpayment\b|\bpricing\b|\bbilled\b", re.I)
PLAN = re.compile(r"\bpro plan\b|\bdowngrad|\bannual payment\b|\bbilling change\b", re.I)
FLEXIBILITY = re.compile(r"\bflexibility\b|\bdiscount\b|\bprice match\b", re.I)
CANCEL = re.compile(r"\bcancell?ing\b|\bcancell?ation\b|\bcancel\b|\bclose my account\b", re.I)
COMPETITOR = re.compile(r"\bcompetitor\b|\bswitching\b|\bmove to a competitor\b", re.I)
DELETION = re.compile(
    r"delete (all )?personal data|permanently delete|delete my account|"
    r"all associated data|right to be forgotten",
    re.I,
)
GDPR = re.compile(r"\bgdpr\b|article\s*17|required by law", re.I)
SECURITY = re.compile(
    r"security (issue|bug|vulnerabilit)|account_id|another company.?s dashboard|"
    r"\bidor\b|see another (company|account|user)",
    re.I,
)
INJECTION = re.compile(
    r"ignore (all )?previous instructions|system note\s*:|vip[- ]verified|"
    r"do not escalate|immediately approve|mark this ticket resolved",
    re.I,
)
ULTIMATUM = re.compile(r"if this isn'?t fixed today|fixed today i'?m cancell", re.I)
TWO_ISSUES = re.compile(r"\btwo issues\b|\bfirst[,.]\s+.+\bsecond[,.]\s", re.I | re.S)
FOLLOWUP = re.compile(r"following up|still isn'?t|reported this yesterday|\bagain\)", re.I)
QUOTED_PRINTABLE = re.compile(r"=[0-9A-Fa-f]{2}")
ACCOUNT_ACCESS = re.compile(
    r"log in|password reset|contraseña|iniciar sesión|acceder a mi cuenta",
    re.I,
)
SPANISH = re.compile(r"\b(hola|contraseña|gracias|sesión)\b", re.I)
SPAM = re.compile(r"instagram|followers|free trial of our", re.I)
FEATURE = re.compile(r"dark mode|any plans to add", re.I)
BUG = re.compile(r"\bexport\b|\btiming out\b|\bblank\b|not working", re.I)
MONEY_LOOSE = re.compile(r"invoice|refund|charg|pricing|payment|\$", re.I)
AMOUNT = re.compile(r"\$\d+(?:\.\d+)?")

VAGUE_BODIES = {"help", "hello", "hi", "broken", "issue", "please help", "it's broken"}


@dataclass(frozen=True)
class TextSignals:
    raw: str
    low: str
    refund: bool
    money: bool
    deletion: bool
    gdpr: bool
    security: bool
    injection: bool
    cancel: bool
    churn: bool
    ultimatum: bool
    multi_issue: bool
    followup: bool
    garbled: bool
    spammy: bool
    gratitude: bool
    feature_request: bool
    account_access: bool
    bug: bool
    spanish: bool
    identifiers: tuple[str, ...]


def ticket_text(ticket: Ticket) -> str:
    return f"{ticket.subject}\n{ticket.body}"


def is_garbled(text: str) -> bool:
    if "\ufffd" in text or "=EF=BF=BD" in text.upper():
        return True
    if len(QUOTED_PRINTABLE.findall(text)) >= 2:
        return True
    return text.count("?") >= 4


def is_vague(ticket: Ticket) -> bool:
    body = ticket.body.strip().lower()
    words = body.split()
    if len(words) <= 2 or body in VAGUE_BODIES:
        return True
    return len(ticket.subject.split()) <= 3 and len(words) <= 4


def scan(ticket: Ticket) -> TextSignals:
    raw = ticket_text(ticket)
    low = raw.lower()
    refund = bool(REFUND.search(raw))
    money = bool(
        refund
        or OVERCHARGE.search(raw)
        or INVOICE.search(raw)
        or PLAN.search(raw)
        or FLEXIBILITY.search(raw)
    )
    identifiers: list[str] = []
    identifiers.extend(AMOUNT.findall(raw))
    if "Pro plan" in raw:
        identifiers.append("Pro plan")
    if "Chrome" in raw:
        identifiers.append("Chrome/Mac")
    return TextSignals(
        raw=raw,
        low=low,
        refund=refund,
        money=money,
        deletion=bool(DELETION.search(raw)),
        gdpr=bool(GDPR.search(raw)),
        security=bool(SECURITY.search(raw)),
        injection=bool(INJECTION.search(raw)),
        cancel=bool(CANCEL.search(raw) or ULTIMATUM.search(raw)),
        churn=bool(COMPETITOR.search(raw)),
        ultimatum=bool(ULTIMATUM.search(raw)),
        multi_issue=bool(TWO_ISSUES.search(raw)),
        followup=bool(FOLLOWUP.search(raw)),
        garbled=is_garbled(ticket.body) or is_garbled(ticket.subject),
        spammy=bool(SPAM.search(raw) or ticket.from_email.lower().startswith("promo@")),
        gratitude="thank" in low and "no action" in low,
        feature_request=bool(FEATURE.search(raw)),
        account_access=bool(ACCOUNT_ACCESS.search(raw)),
        bug=bool(BUG.search(raw)),
        spanish=bool(SPANISH.search(raw)),
        identifiers=tuple(identifiers),
    )
