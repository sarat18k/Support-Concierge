"""LangGraph pipeline: classify → extract → policy → decide → (draft) → persist.

The graph is compiled once per Pipeline instance and reused across tickets.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from concierge.agents.classifier import classify
from concierge.agents.decision import decide
from concierge.agents.drafter import draft
from concierge.agents.extractor import extract
from concierge.models import (
    Action,
    Classification,
    Extraction,
    PolicyResult,
    Ticket,
)
from concierge.persistence import store
from concierge.pipeline.state import GraphState, initial_state
from concierge.policy import apply_policy


def _dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _ticket(state: GraphState) -> Ticket:
    return Ticket.model_validate(state["ticket"])


def _parse(model_cls, payload):
    return model_cls.model_validate(payload) if payload else None


def _failures(state: GraphState, agent: str, error: str) -> list[dict[str, Any]]:
    items = list(state.get("failures") or [])
    items.append({"agent": agent, "error": error, "recovered": True})
    return items


def build_graph(conn: sqlite3.Connection):
    def classifier_node(state: GraphState) -> dict[str, Any]:
        ticket = _ticket(state)
        started = time.perf_counter()
        try:
            result = classify(ticket)
            store.record_step(
                conn, ticket.id, "classifier", _dump(result), result.confidence, _ms(started)
            )
            return {"classification": _dump(result), "skip_remaining_llm": False}
        except Exception as exc:  # noqa: BLE001
            fallback = Classification(
                category="unclear",
                language="und",
                confidence=0.1,
                rationale=f"Classifier failed; using unclear fallback. ({exc})",
            )
            store.record_step(
                conn, ticket.id, "classifier", _dump(fallback), fallback.confidence,
                _ms(started), error=str(exc),
            )
            return {
                "classification": _dump(fallback),
                "skip_remaining_llm": True,
                "failed": True,
                "failures": _failures(state, "classifier", str(exc)),
            }

    def extractor_node(state: GraphState) -> dict[str, Any]:
        ticket = _ticket(state)
        started = time.perf_counter()
        if state.get("skip_remaining_llm"):
            fallback = Extraction(
                confidence=0.1,
                rationale="Skipped because an upstream LLM agent failed.",
            )
            store.record_step(
                conn, ticket.id, "extractor", _dump(fallback), fallback.confidence,
                _ms(started), error="skipped_upstream_failure",
            )
            return {"extraction": _dump(fallback)}
        try:
            result = extract(ticket)
            store.record_step(
                conn, ticket.id, "extractor", _dump(result), result.confidence, _ms(started)
            )
            return {"extraction": _dump(result)}
        except Exception as exc:  # noqa: BLE001
            fallback = Extraction(
                confidence=0.1,
                rationale=f"Extractor failed; policy will use raw-text scans. ({exc})",
            )
            store.record_step(
                conn, ticket.id, "extractor", _dump(fallback), fallback.confidence,
                _ms(started), error=str(exc),
            )
            return {
                "extraction": _dump(fallback),
                "skip_remaining_llm": True,
                "failed": True,
                "failures": _failures(state, "extractor", str(exc)),
            }

    def policy_node(state: GraphState) -> dict[str, Any]:
        ticket = _ticket(state)
        started = time.perf_counter()
        result = apply_policy(
            ticket,
            _parse(Classification, state.get("classification")),
            _parse(Extraction, state.get("extraction")),
        )
        store.record_step(conn, ticket.id, "policy_guard", _dump(result), None, _ms(started))
        return {"policy": _dump(result)}

    def decision_node(state: GraphState) -> dict[str, Any]:
        ticket = _ticket(state)
        started = time.perf_counter()
        result = decide(
            ticket,
            _parse(Classification, state.get("classification")),
            _parse(Extraction, state.get("extraction")),
            PolicyResult.model_validate(state["policy"]),
            failed=bool(state.get("failed")),
        )
        payload = _dump(result)
        store.record_step(
            conn, ticket.id, "decision", payload, result.composite_confidence, _ms(started)
        )
        store.record_decision(
            conn,
            ticket.id,
            result.action.value,
            result.composite_confidence,
            result.model_confidence,
            result.heuristic_deltas,
            result.policy_flags,
            result.thresholds,
            result.rationale,
        )
        return {"decision": payload, "action": result.action.value}

    def route_after_decision(state: GraphState) -> Literal["drafter", "persist"]:
        # Escalate skips drafting — brief: no auto-generated response.
        action = state.get("action") or (state.get("decision") or {}).get("action")
        return "persist" if action == Action.ESCALATE.value else "drafter"

    def drafter_node(state: GraphState) -> dict[str, Any]:
        ticket = _ticket(state)
        started = time.perf_counter()
        try:
            result = draft(
                ticket,
                Action(state["action"]),
                _parse(Classification, state.get("classification")),
                _parse(Extraction, state.get("extraction")),
            )
            store.record_step(
                conn, ticket.id, "drafter", _dump(result), result.confidence, _ms(started)
            )
            return {"draft": _dump(result)}
        except Exception as exc:  # noqa: BLE001
            store.record_step(
                conn, ticket.id, "drafter", {}, None, _ms(started), error=str(exc)
            )
            return {
                "draft": None,
                "action": Action.ESCALATE.value,
                "failed": True,
                "skip_remaining_llm": True,
                "failures": _failures(state, "drafter", str(exc)),
            }

    def persist_node(state: GraphState) -> dict[str, Any]:
        ticket = _ticket(state)
        action = state.get("action") or Action.ESCALATE.value
        classification = state.get("classification") or {}
        decision = state.get("decision") or {}
        draft_body = (state.get("draft") or {}).get("body") if state.get("draft") else None

        if action == Action.AUTO_RESOLVE.value:
            status = "closed"
        elif action == Action.DRAFT_FOR_REVIEW.value:
            status = "pending_review"
        else:
            status = "escalated"
            action = Action.ESCALATE.value
            draft_body = None  # brief: escalate has no auto-generated response

        store.finalize_ticket(
            conn,
            ticket.id,
            classification.get("category"),
            action,
            status,
            decision.get("composite_confidence"),
            draft_body,
        )
        payload = {
            "action": action,
            "status": status,
            "decision_rationale": decision.get("rationale"),
            "policy": state.get("policy"),
            "draft": draft_body,
            "failures": state.get("failures") or [],
        }
        if action != Action.AUTO_RESOLVE.value:
            store.enqueue(conn, ticket.id, action, payload)
        return {"status": status, "action": action}

    graph = StateGraph(GraphState)
    graph.add_node("classifier", classifier_node)
    graph.add_node("extractor", extractor_node)
    graph.add_node("policy", policy_node)
    graph.add_node("decision", decision_node)
    graph.add_node("drafter", drafter_node)
    graph.add_node("persist", persist_node)
    graph.add_edge(START, "classifier")
    graph.add_edge("classifier", "extractor")
    graph.add_edge("extractor", "policy")
    graph.add_edge("policy", "decision")
    graph.add_conditional_edges(
        "decision", route_after_decision, {"drafter": "drafter", "persist": "persist"}
    )
    graph.add_edge("drafter", "persist")
    graph.add_edge("persist", END)
    return graph.compile()


class Pipeline:
    """Reusable compiled graph bound to one SQLite connection."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.app = build_graph(conn)

    def run(self, ticket: dict[str, Any]) -> GraphState:
        store.ingest_ticket(self.conn, ticket)
        return self.app.invoke(initial_state(ticket))
