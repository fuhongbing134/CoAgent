"""Small reusable HTTP boundary for user task operations."""

import json
import secrets
import sqlite3


def handle_action_request(handler, store, token, on_change=None):
    """POST only. Host, Origin and per-process token are mandatory."""
    host = handler.headers.get("Host", "")
    port = handler.server.server_port
    if (host not in {f"127.0.0.1:{port}", f"localhost:{port}"}
            or handler.headers.get("Origin") != "http://" + host
            or not secrets.compare_digest(handler.headers.get("X-Capability-Token", ""), token)):
        handler.send_json(403, {"error": "same-origin authorization required"})
        return
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        if not 0 < length <= 100000:
            raise ValueError("invalid content length")
        payload = json.loads(handler.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON object required")
        allowed = {"job_id", "conversation_id", "operation_id", "action", "content", "round_limit"}
        if payload.keys() - allowed:
            raise ValueError("unknown request fields")
        job_id, conversation_id = payload.get("job_id"), payload.get("conversation_id")
        if any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in (job_id, conversation_id)):
            raise ValueError("job_id/conversation_id required")
        if payload.get("action") == "view":
            handler.send_json(200, store.view(job_id, conversation_id))
        else:
            changed = store.operate(job_id, conversation_id, payload.get("operation_id"), payload.get("action"),
                                    payload.get("content", ""), payload.get("round_limit"))
            if changed and on_change:
                on_change(job_id)
            handler.send_json(200, {"changed": changed, "task": store.view(job_id, conversation_id),
                                    "dispatch_pending": True})
    except (ValueError, UnicodeDecodeError, TypeError) as error:
        handler.send_json(400, {"error": str(error)})
    except sqlite3.Error:
        handler.send_json(503, {"error": "task storage unavailable"})
