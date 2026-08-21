"""Shared Pydantic models for tickets, agent outputs, and routing."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Category(str, Enum):
    BILLING = "billing"
    BUG = "bug"
    FEATURE_REQUEST = "feature_request"
    ACCOUNT_ACCESS = "account_access"
    SECURITY = "security"
    LEGAL_PRIVACY = "legal_privacy"
    CANCELLATION = "cancellation"
    SPAM = "spam"
    GRATITUDE = "gratitude"
    CHURN_RISK = "churn_risk"
    UNCLEAR = "unclear"


class Action(str, Enum):
    AUTO_RESOLVE = "auto_resolve"
    DRAFT_FOR_REVIEW = "draft_for_review"
    ESCALATE = "escalate"


class Ticket(BaseModel):
    id: str
    received_at: str
    from_name: str
    from_email: str
    subject: str
    body: str


class Classification(BaseModel):
    category: Category
    language: str = "en"
    multi_intent: bool = False
    prompt_injection: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str


class Extraction(BaseModel):
    product_area: str | None = None
    urgency: Literal["low", "medium", "high", "critical"] = "medium"
    sentiment: Literal["positive", "neutral", "negative", "angry"] = "neutral"
    identifiers: list[str] = Field(default_factory=list)
    is_followup: bool = False
    secondary_intents: list[str] = Field(default_factory=list)
    money_mentioned: bool = False
    refund_requested: bool = False
    cancellation_threatened: bool = False
    deletion_requested: bool = False
    legal_language: bool = False
    security_report: bool = False
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    rationale: str = ""


class PolicyResult(BaseModel):
    allow_auto_resolve: bool
    force_escalate: bool
    flags: list[str] = Field(default_factory=list)
    rationale: str


class ConfidenceBreakdown(BaseModel):
    model_confidence: float
    heuristic_deltas: dict[str, float] = Field(default_factory=dict)
    composite: float
    vague: bool = False
    garbled: bool = False


class DecisionResult(BaseModel):
    action: Action
    composite_confidence: float
    model_confidence: float
    heuristic_deltas: dict[str, float] = Field(default_factory=dict)
    thresholds: dict[str, float] = Field(default_factory=dict)
    policy_flags: list[str] = Field(default_factory=list)
    rationale: str


class DraftResult(BaseModel):
    body: str
    template_used: str | None = None
    language: str = "en"
    confidence: float = 0.8
    rationale: str = ""
