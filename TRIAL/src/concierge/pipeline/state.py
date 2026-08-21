"""LangGraph state for one ticket pass."""

from __future__ import annotations

from typing import Any, TypedDict


class GraphState(TypedDict, total=False):
    ticket: dict[str, Any]
    classification: dict[str, Any] | None
    extraction: dict[str, Any] | None
    policy: dict[str, Any] | None
    decision: dict[str, Any] | None
    draft: dict[str, Any] | None
    skip_remaining_llm: bool
    failed: bool
    failures: list[dict[str, Any]]
    action: str
    status: str


def initial_state(ticket: dict[str, Any]) -> GraphState:
    return {
        "ticket": ticket,
        "classification": None,
        "extraction": None,
        "policy": None,
        "decision": None,
        "draft": None,
        "skip_remaining_llm": False,
        "failed": False,
        "failures": [],
    }
