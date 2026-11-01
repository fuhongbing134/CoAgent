"""Durable user operations and outbox; consumers must confirm real cancellation."""

import json
import sqlite3
from contextlib import closing

from collaboration_lifecycle import LifecycleStore
from git_workflow_gate import GIT_START_GATE


class ActionStore(LifecycleStore):
    def __init__(self, path, registry=None, *, finalize_message=None):
        super().__init__(path, registry, finalize_message=finalize_message)

    def bind(self, job_id, conversation_id):
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise ValueError("conversation_id required")
        with self.transaction() as db:
            self._job(db, job_id)
            prior = db.execute("SELECT conversation_id FROM collab_bindings WHERE job_id=?", (job_id,)).fetchone()
            if prior and prior[0] != conversation_id:
                raise ValueError("job already belongs to another conversation")
            db.execute("INSERT OR IGNORE INTO collab_bindings VALUES (?,?)", (job_id,conversation_id))

    def _authorize(self, db, job_id, conversation_id):
        binding = db.execute("SELECT conversation_id FROM collab_bindings WHERE job_id=?", (job_id,)).fetchone()
        if binding is None or binding[0] != conversation_id:
            raise ValueError("job does not belong to conversation")

    def operate(self, job_id, conversation_id, operation_id, action, content="", round_limit=None):
        if not isinstance(operation_id, str) or not operation_id.strip() or len(operation_id) > 200:
            raise ValueError("operation_id required (max 200 chars)")
        if action not in {"answer", "append", "finalize", "stop", "set_limit"}:
            raise ValueError("unknown user action")
        if not isinstance(content, str) or len(content) > 20000:
            raise ValueError("invalid user content")
        if action in {"answer", "append"} and not content.strip():
            raise ValueError("nonempty answer/update required")
        if action == "set_limit" and (type(round_limit) is not int or not 2 <= round_limit <= 100):
            raise ValueError("round_limit must be integer 2..100")
        if action != "set_limit" and round_limit is not None:
            raise ValueError("round_limit only allowed for set_limit")
        body = json.dumps(dict(action=action,content=content,round_limit=round_limit),sort_keys=True)
        with GIT_START_GATE, self.transaction() as db:
            self._authorize(db, job_id, conversation_id)
            prior = db.execute("SELECT body FROM collab_operations WHERE job_id=? AND operation_id=?", (job_id,operation_id)).fetchone()
            if prior:
                if prior[0] != body:
                    raise ValueError("operation_id reused with different action")
                return False
            job, life, plan = self._life(db, job_id)
            phase = life["phase"]
            if phase in {"completed", "failed", "interrupted", "quiescing"} and action != "stop":
                raise ValueError("task cannot accept this action in current phase")
            event = None
            if action == "answer":
                if phase not in {"waiting_user", "blocked"}:
                    raise ValueError("task is not waiting for user")
                needed = 1 if plan["owner_id"] == plan["final_responder_id"] else 2
                if life["used"] + needed > life["round_limit"]:
                    raise ValueError("insufficient continuation budget; finalize or raise limit")
                db.execute("UPDATE collab_lifecycle SET phase='working',questions='[]',reason='' WHERE job_id=?", (job_id,))
                event = "resume_owner"
            elif action == "append":
                if phase not in {"working", "waiting_user", "blocked"}:
                    raise ValueError("cannot update during delivery")
            elif action == "set_limit":
                if round_limit <= life["used"]:
                    raise ValueError("limit must leave delivery capacity")
                db.execute("UPDATE collab_lifecycle SET round_limit=? WHERE job_id=?", (round_limit,job_id))
            elif action in {"stop", "finalize"}:
                if action == "finalize" and (phase not in {"working", "waiting_user", "blocked", "final_ready"} or life["used"] >= life["round_limit"]):
                    raise ValueError("cannot finalize in current phase/budget")
                db.execute("UPDATE collab_events SET state='cancelled' WHERE job_id=? AND state IN ('pending','claimed')", (job_id,))
                db.execute("UPDATE collab_calls SET state='cancelled' WHERE job_id=? AND state='reserved'", (job_id,))
                db.execute("UPDATE collab_tasks SET state='cancelled' WHERE job_id=? AND state IN ('pending','queued','running')", (job_id,))
                db.execute("UPDATE collab_lifecycle SET phase='quiescing',reason=? WHERE job_id=?", (content or action,job_id))
                if action == "stop":
                    db.execute("UPDATE collab_jobs SET stopped=1 WHERE job_id=?", (job_id,))
                event = "cancel_then_stop" if action == "stop" else "cancel_then_finalize"
            if action in {"answer", "append"}:
                db.execute("INSERT INTO collab_user_updates VALUES (?,?,?)", (job_id,operation_id,content))
            db.execute("INSERT INTO collab_operations VALUES (?,?,?)", (job_id,operation_id,body))
            if event:
                db.execute("INSERT INTO collab_events(job_id,operation_id,kind) VALUES (?,?,?)", (job_id,operation_id,event))
            return True

    def view(self, job_id, conversation_id):
        with self.transaction() as db:
            self._authorize(db, job_id, conversation_id)
            job = self._job(db, job_id)
            life = db.execute("SELECT * FROM collab_lifecycle WHERE job_id=?", (job_id,)).fetchone()
            if life is None:
                raise ValueError("lifecycle not initialized")
            return dict(state={**dict(life), "phase": "stopped" if job["stopped"] else life["phase"]},
                        request=job["request"], plan=json.loads(job["plan"]),
                        updates=[dict(r) for r in db.execute("SELECT operation_id,content FROM collab_user_updates WHERE job_id=? ORDER BY rowid", (job_id,))],
                        events=[dict(r) for r in db.execute("SELECT operation_id,kind,state FROM collab_events WHERE job_id=? ORDER BY rowid", (job_id,))])

    def delivery_material(self, job_id):
        material = super().delivery_material(job_id)
        with self.transaction() as db:
            material["user_updates"] = [dict(r) for r in db.execute(
                "SELECT operation_id,content FROM collab_user_updates WHERE job_id=? ORDER BY rowid", (job_id,))]
        return material

    def confirm_quiescent(self, job_id, operation_id):
        """Internal only: invoke after transport confirms no process can write further.

        This is not an HTTP user action. It does not kill processes itself.
        """
        with self.transaction() as db:
            job = self._job(db, job_id)
            event = db.execute("SELECT * FROM collab_events WHERE job_id=? AND operation_id=?", (job_id,operation_id)).fetchone()
            if event is None or event["kind"] not in {"cancel_then_stop", "cancel_then_finalize"}:
                raise ValueError("unknown cancellation event")
            if event["state"] in {"done", "cancelled"}:
                return False
            db.execute("UPDATE collab_events SET state='done' WHERE job_id=? AND operation_id=?", (job_id,operation_id))
            if job["stopped"]:
                return True
            db.execute("UPDATE collab_lifecycle SET phase='final_ready' WHERE job_id=? AND phase='quiescing'", (job_id,))
            return True
