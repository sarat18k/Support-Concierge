"""CLI: run the pipeline and act on human-review queues."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from concierge.llm import llm_available, model_name
from concierge.paths import results_dir, sample_tickets_path
from concierge.pipeline import Pipeline
from concierge.persistence import store


def _load_tickets(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _pretty_json(raw: str) -> str:
    try:
        return json.dumps(json.loads(raw), indent=2)
    except (TypeError, json.JSONDecodeError):
        return raw


def cmd_run(args: argparse.Namespace) -> int:
    tickets = _load_tickets(Path(args.tickets) if args.tickets else sample_tickets_path())
    conn = store.init_db()
    pipeline = Pipeline(conn)
    results = []
    backend = model_name() if llm_available() else "heuristic-offline"
    print(f"Running {len(tickets)} ticket(s) with {backend}...")
    for ticket in tickets:
        print(f"  {ticket['id']}  {ticket['subject'][:60]}", flush=True)
        state = pipeline.run(ticket)
        action = state.get("action")
        conf = (state.get("decision") or {}).get("composite_confidence")
        print(f"    -> {action}  confidence={conf}  status={state.get('status')}")
        results.append(
            {
                "id": ticket["id"],
                "subject": ticket["subject"],
                "from_name": ticket["from_name"],
                "from_email": ticket["from_email"],
                "action": action,
                "status": state.get("status"),
                "category": (state.get("classification") or {}).get("category"),
                "composite_confidence": conf,
                "policy_flags": (state.get("policy") or {}).get("flags"),
                "decision_rationale": (state.get("decision") or {}).get("rationale"),
                "classification": state.get("classification"),
                "extraction": state.get("extraction"),
                "policy": state.get("policy"),
                "decision": state.get("decision"),
                "draft": state.get("draft"),
                "failures": state.get("failures") or [],
            }
        )

    out_path = Path(args.out) if args.out else results_dir() / "sample_run.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "run_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "model": backend,
                "ticket_count": len(results),
                "tickets": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nWrote {out_path}")
    counts: dict[str, int] = {}
    for row in results:
        key = row.get("action") or "unknown"
        counts[key] = counts.get(key, 0) + 1
    print("\nAction summary:")
    for action, n in sorted(counts.items()):
        print(f"  {action:20} {n}")
    return 0


def cmd_queue(args: argparse.Namespace) -> int:
    rows = store.list_queue(store.init_db(), open_only=not args.all)
    if not rows:
        print("Queue is empty.")
        return 0
    print(f"{'ID':10}  {'QUEUE':18}  {'STATUS':16}  {'FROM':22}  SUBJECT")
    print("-" * 100)
    for row in rows:
        if args.type and row["queue_type"] != args.type:
            continue
        print(
            f"{row['ticket_id']:10}  {row['queue_type']:18}  "
            f"{row['status']:16}  {row['from_name'][:22]:22}  {row['subject'][:48]}"
        )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    audit = store.get_ticket_audit(store.init_db(), args.ticket_id)
    if audit is None:
        print(f"No ticket {args.ticket_id}", file=sys.stderr)
        return 1
    ticket = audit["ticket"]
    print(f"Ticket {ticket['id']}  status={ticket['status']}  action={ticket['action']}")
    print(f"From: {ticket['from_name']} <{ticket['from_email']}>")
    print(f"Subject: {ticket['subject']}")
    print(f"\n--- body ---\n{ticket['body']}\n")
    if audit["decision"]:
        d = audit["decision"]
        print("--- decision ---")
        print(f"action={d['action']}  composite={d['composite_confidence']}")
        print(f"flags={d['policy_flags_json']}")
        print(f"deltas={d['heuristic_deltas_json']}")
        print(f"rationale={d['rationale']}\n")
    print("--- agent steps ---")
    for step in audit["steps"]:
        err = f"  ERROR={step['error']}" if step["error"] else ""
        print(
            f"[{step['agent_name']}] conf={step['confidence']} "
            f"latency_ms={step['latency_ms']}{err}"
        )
        print(_pretty_json(step["output_json"]))
        print()
    if ticket.get("final_draft"):
        print("--- draft ---")
        print(ticket["final_draft"])
    return 0


def _hitl(ticket_id: str, action: str, reason: str | None, body: str | None = None) -> int:
    ok = store.apply_human_action(
        store.init_db(), ticket_id, action, reason, edited_body=body
    )
    if not ok:
        print(f"No open queue item for {ticket_id}", file=sys.stderr)
        return 1
    print(f"{action.capitalize()} {ticket_id}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="concierge",
        description="Support Concierge - multi-agent ticket triage",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="Ingest tickets and run the pipeline")
    run.add_argument("--tickets", help="Path to tickets JSON")
    run.add_argument("--out", help="Results JSON path")
    run.set_defaults(func=cmd_run)

    queue = sub.add_parser("queue", help="List draft-for-review and escalate queues")
    queue.add_argument("--all", action="store_true", help="Include resolved items")
    queue.add_argument("--type", choices=["draft_for_review", "escalate"])
    queue.set_defaults(func=cmd_queue)

    show = sub.add_parser("show", help="Print the audit trail for a ticket")
    show.add_argument("ticket_id")
    show.set_defaults(func=cmd_show)

    approve = sub.add_parser("approve", help="Approve a queued item")
    approve.add_argument("ticket_id")
    approve.add_argument("--reason", default=None)
    approve.set_defaults(func=lambda a: _hitl(a.ticket_id, "approved", a.reason))

    edit = sub.add_parser("edit", help="Replace the draft and approve")
    edit.add_argument("ticket_id")
    edit.add_argument("--body", required=True)
    edit.add_argument("--reason", default="human edit")
    edit.set_defaults(func=lambda a: _hitl(a.ticket_id, "edited", a.reason, a.body))

    reject = sub.add_parser("reject", help="Reject a queued item")
    reject.add_argument("ticket_id")
    reject.add_argument("--reason", default="rejected")
    reject.set_defaults(func=lambda a: _hitl(a.ticket_id, "rejected", a.reason))

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    raise SystemExit(args.func(args))
