#!/usr/bin/env python3
"""Validated conversational feedback events and replayable preference projections.

The host agent interprets language; this module enforces targets, provenance,
idempotency and learning rules. No network, model calls or chat surveillance.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


POLICY_VERSION = 1
MIN_FOLLOW_UPS = 2
SIGNALS = {"follow_up", "interested", "not_interested", "neutral", "saved",
           "read", "temporarily_unavailable", "retract"}
FACET_REASONS = {"too_basic", "too_hard", "not_actionable", "source", "already_known"}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def project_ids(record):
    return set(record.get("project_ids", [])) | {
        m["project_id"] for m in record.get("project_matches", [])
        if m.get("project_id") and m.get("matched_terms")
    }


def terms_of(record):
    return list(dict.fromkeys(str(t) for key in ("topics", "keywords")
                              for t in record.get(key, []) if t))[:24]


def record_event(history, decision):
    """Return a copy and a result; never change storage on rejection or preview."""
    message = decision.get("message", {})
    if message.get("role") != "user":
        raise ValueError("Feedback evidence must be a user message")
    for key in ("id", "conversation_id", "content"):
        if not isinstance(message.get(key), str) or not message[key].strip():
            raise ValueError(f"message.{key} is required")
    quote = decision.get("evidence_quote", "")
    if not isinstance(quote, str) or not quote.strip() or quote not in message["content"]:
        raise ValueError("evidence_quote must be an exact nonempty substring of the user message")
    signal = decision.get("signal")
    if signal not in SIGNALS:
        raise ValueError("Unsupported signal")
    rec = next((r for r in history.get("recommendations", [])
                if r["recommendation_id"] == decision.get("recommendation_id")), None)
    if rec is None:
        raise ValueError("Unknown recommendation_id; do not guess the target")
    if rec.get("conversation_id") != message["conversation_id"]:
        raise ValueError("Recommendation and feedback must belong to the same conversation")

    known_projects = project_ids(rec)
    scope = decision.get("scope", "project" if len(known_projects) == 1 else "paper")
    if scope not in {"paper", "project", "global"}:
        raise ValueError("scope must be paper, project or global")
    project = decision.get("project_id")
    if scope == "project":
        if project is None and len(known_projects) == 1:
            project = next(iter(known_projects))
        if project not in known_projects:
            raise ValueError("Project must be unambiguously linked to the recommendation")
    elif project:
        raise ValueError("project_id only applies to project scope")
    if scope == "global" and signal not in {"interested", "not_interested", "retract"}:
        raise ValueError("Only explicit preferences may have global scope")

    terms = decision.get("terms", terms_of(rec) if scope == "project" else [])
    if not isinstance(terms, list) or any(not isinstance(t, str) or not t.strip() for t in terms):
        raise ValueError("terms must be nonempty strings")
    terms = list(dict.fromkeys(terms))
    if scope == "project" and not set(terms).issubset(terms_of(rec)):
        raise ValueError("Project terms must come from the recorded paper metadata")
    if scope == "global" and signal != "retract":
        if not terms or any(t.casefold() not in quote.casefold() for t in terms):
            raise ValueError("Global terms must be explicitly named in the user evidence")
    reason = decision.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise ValueError("reason must be a string or null")
    # Difficulty/source complaints are not evidence of disliking the topic.
    if signal == "not_interested" and reason in FACET_REASONS:
        scope, project, terms = "paper", None, []
    if scope == "paper":
        terms = []

    events = history.get("feedback_events", [])
    target = decision.get("retracts")
    if signal == "retract":
        old = next((e for e in events if e["event_id"] == target), None)
        if old is None or old["paper_id"] != rec["paper_id"] or old["signal"] == "retract":
            raise ValueError("Retraction must reference an existing non-retraction event for this paper")
    elif target:
        raise ValueError("retracts only applies to a retraction")
    # A message can discuss multiple papers, but retries cannot reinforce one paper.
    identity = [message["conversation_id"], message["id"], rec["paper_id"]]
    event_id = "fb-" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]
    event = {
        "event_id": event_id, "recommendation_id": rec["recommendation_id"],
        "paper_id": rec["paper_id"], "conversation_id": message["conversation_id"],
        "message_id": message["id"], "source": "user", "evidence_quote": quote,
        "signal": signal, "scope": scope, "project_id": project,
        "terms": terms, "reason": reason, "dimension": decision.get("dimension"),
        "retracts": target, "policy_version": POLICY_VERSION,
    }
    existing = next((e for e in events if e["event_id"] == event_id), None)
    if existing:
        if {k: v for k, v in existing.items() if k != "created_at"} != event:
            raise ValueError("Message already recorded differently; retract it with a new user message")
        return copy.deepcopy(history), {"event": "NONE", "event_id": event_id}
    event["created_at"] = now_iso()
    updated = copy.deepcopy(history)
    updated.setdefault("feedback_events", []).append(event)
    updated["feedback_policy_version"] = POLICY_VERSION
    return updated, {"event": "ADD", "event_id": event_id,
                     "preferences": derive_preferences(updated)}


def derive_preferences(history):
    """Replay events. At most one contribution per paper/scope, not per message."""
    events = history.get("feedback_events", [])
    revoked = {e["retracts"] for e in events if e["signal"] == "retract"}
    latest_explicit = {}
    for e in events:
        if e["event_id"] not in revoked and e["signal"] in {"interested", "not_interested"}:
            latest_explicit[e["paper_id"]] = e
    groups = defaultdict(list)
    for e in events:
        if e["event_id"] not in revoked and e["signal"] in {"follow_up", "interested", "not_interested"}:
            groups[(e["paper_id"], e["scope"], e.get("project_id"))].append(e)
    preferences = []
    for (paper, scope, project), group in groups.items():
        latest = latest_explicit.get(paper)
        if scope == "paper" and latest and latest["scope"] != "paper":
            continue  # A later explicit scoped judgment supersedes the old paper verdict.
        if scope != "paper" and latest and latest["scope"] == "paper" and latest["signal"] == "not_interested":
            continue
        explicit = [e for e in group if e["signal"] in {"interested", "not_interested"}]
        if explicit:
            chosen = explicit[-1]
            supporting = [chosen]
            strength = 1.0 if chosen["signal"] == "interested" else -1.0
            kind = "explicit"
        else:
            sessions = defaultdict(list)
            for e in group:
                sessions[e["conversation_id"]].append(e)
            eligible = [s for s in sessions.values() if len({e["message_id"] for e in s}) >= MIN_FOLLOW_UPS]
            if not eligible:
                continue
            supporting = max(eligible, key=lambda s: s[-1]["created_at"])[-MIN_FOLLOW_UPS:]
            chosen = supporting[-1]
            strength, kind = 0.35, "implicit"
        preferences.append({
            "paper_id": paper, "scope": scope, "project_id": project,
            "terms": list(dict.fromkeys(t for e in supporting for t in e["terms"])),
            "strength": strength, "kind": kind, "reason": chosen.get("reason"),
            "updated_at": chosen["created_at"],
            "evidence_ids": [e["event_id"] for e in supporting],
        })
    # A direct paper-level rejection also overrides implicit project-level engagement.
    rejected_papers = {p["paper_id"] for p in preferences
                       if p["scope"] == "paper" and p["strength"] < 0}
    return [p for p in preferences if not (p["kind"] == "implicit" and p["paper_id"] in rejected_papers)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", required=True, type=Path)
    parser.add_argument("--decision", required=True, type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    try:
        history = json.loads(args.history.read_text(encoding="utf-8"))
        decision = json.loads(args.decision.read_text(encoding="utf-8"))
        updated, result = record_event(history, decision)
        if args.write and result["event"] != "NONE":
            atomic_write(args.history, updated)
        print(json.dumps({**result, "written": args.write and result["event"] != "NONE"}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
