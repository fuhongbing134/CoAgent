"""Version 1 collaboration payload validation (no scheduling or semantic inference)."""

import copy
import json

from agent_registry import default_registry

STATUSES = {"continue_work", "request_collaboration", "completed", "waiting_user", "blocked"}
HANDOFF_FIELDS = {"original_question", "response_or_changes", "evidence", "rejected_alternatives", "uncertainties", "expected_action"}
MISSING_PLAN = object()


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - set(optional):
        raise ValueError(f"expected fields: {sorted(required)}; optional: {sorted(optional)}")


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected nonempty text")


def _validate_plan_structure(plan, registry, allow_empty=False):
    """Validate fields and normalize dependencies, without resolving their IDs."""
    _keys(plan, {"owner_id", "final_responder_id", "tasks"})
    registry.require(plan["owner_id"])
    registry.require(plan["final_responder_id"])
    tasks = plan["tasks"]
    if not isinstance(tasks, list) or (not tasks and not allow_empty):
        raise ValueError("tasks must be a nonempty list")
    graph = {}
    for task in tasks:
        _keys(task, {"task_id", "agent_id", "instruction"}, {"depends_on"})
        _text(task["task_id"])
        _text(task["instruction"])
        registry.require(task["agent_id"])
        deps = task.setdefault("depends_on", [])
        if not isinstance(deps, list) or any(not isinstance(d, str) or not d.strip() for d in deps):
            raise ValueError("depends_on must be a list of task IDs")
        if len(set(deps)) != len(deps) or task["task_id"] in graph:
            raise ValueError("duplicate task/dependency")
        graph[task["task_id"]] = deps
    return graph


def validate_plan(plan, registry, allow_empty=False):
    """Validate a complete plan, normalizing omitted dependencies in place."""
    graph = _validate_plan_structure(plan, registry, allow_empty)
    visited, active = set(), set()

    def visit(task_id):
        if task_id not in graph:
            raise ValueError("unknown dependency")
        if task_id in active:
            raise ValueError("cyclic dependency")
        if task_id in visited:
            return
        active.add(task_id)
        for dep in graph[task_id]:
            visit(dep)
        active.remove(task_id)
        visited.add(task_id)

    for task_id in graph:
        visit(task_id)


def normalize_owner_plan(raw_plan, current_plan, *, status, registry=None):
    """Return a complete append-only plan without mutating either input.

    MISSING_PLAN distinguishes omission from an invalid explicit null. Callers
    must retain the raw input for revalidation against the transactional plan.
    """
    registry = registry or default_registry()
    merged = copy.deepcopy(current_plan)
    validate_plan(merged, registry)
    if raw_plan is MISSING_PLAN:
        return merged
    incoming = copy.deepcopy(raw_plan)
    _validate_plan_structure(incoming, registry, allow_empty=(status == "completed"))
    if any(incoming[k] != merged[k] for k in ("owner_id", "final_responder_id")):
        raise ValueError("cannot change roles")
    old_tasks = {t["task_id"]: t for t in merged["tasks"]}
    for task in incoming["tasks"]:
        old = old_tasks.get(task["task_id"])
        if old is not None:
            if old != task:
                raise ValueError("existing task definitions must not change")
        else:
            merged["tasks"].append(task)
    validate_plan(merged, registry)
    return merged


def validate_payload(payload, *, registry=None, require_plan=False, invitation_ids=(), role="owner", phase="collaboration"):
    """invitation_ids are supplied by the later terminal-mention parser, not inferred here."""
    payload = copy.deepcopy(payload)
    registry = registry or default_registry()
    if role not in {"entry", "owner", "collaborator", "final"} or phase not in {"collaboration", "final_delivery"}:
        raise ValueError("invalid invocation context")
    _keys(payload, {"protocol_version", "status", "handoff", "questions"}, {"plan", "blocked_reason"})
    if type(payload["protocol_version"]) is not int or payload["protocol_version"] != 1:
        raise ValueError("unsupported protocol version")
    status = payload["status"]
    if not isinstance(status, str) or status not in STATUSES:
        raise ValueError("invalid status")
    if role == "entry" and status == "completed":
        plan = payload.get("plan")
        tasks = plan.get("tasks", []) if isinstance(plan, dict) else []
        if tasks:
            raise ValueError("Protocol incompatible: entry is planning-only; legacy completed reply with tasks preserved; do not automatically execute/retry. Verify prior effects first.")
    if role in {"collaborator", "final"} and (status == "continue_work" or "plan" in payload):
        raise ValueError("collaborator/final cannot continue or amend the plan")
    if require_plan and "plan" not in payload:
        raise ValueError("initial plan required")
    if "plan" in payload:
        if role == "owner":
            # Dependencies can reference persisted tasks omitted from this input.
            # Full validation is mandatory after normalization at acceptance.
            _validate_plan_structure(payload["plan"], registry, allow_empty=(status == "completed"))
        else:
            validate_plan(payload["plan"], registry, allow_empty=(role == "entry" and status == "completed"))
        if any(t["task_id"] == "__clarify_entry__" for t in payload["plan"]["tasks"]):
            raise ValueError("reserved system task ID: __clarify_entry__")
    _keys(payload["handoff"], HANDOFF_FIELDS)
    for value in payload["handoff"].values():
        _text(value)
    questions = payload["questions"]
    if not isinstance(questions, list):
        raise ValueError("questions must be a list")
    for question in questions:
        _text(question)
    if bool(questions) != (status == "waiting_user"):
        raise ValueError("questions required only for waiting_user")
    if status == "blocked":
        _text(payload.get("blocked_reason"))
    elif "blocked_reason" in payload:
        raise ValueError("blocked_reason only allowed for blocked")
    if not isinstance(invitation_ids, (list, tuple)):
        raise ValueError("invitation_ids must be a sequence")
    for target in invitation_ids:
        registry.require(target)
    if bool(invitation_ids) != (status == "request_collaboration"):
        raise ValueError("status/invitation conflict")
    if status == "request_collaboration" and role not in {"entry", "owner"}:
        raise ValueError("only entry/owner may invite")
    if phase == "final_delivery" and status != "completed":
        raise ValueError("final delivery must complete without further collaboration")
    return payload


def loads_payload(text, **kwargs):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    return validate_payload(json.loads(text, object_pairs_hook=unique_pairs), **kwargs)
