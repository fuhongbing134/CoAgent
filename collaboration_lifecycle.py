"""Stage 4 durable invocation budget and delivery state (no implicit CLI calls)."""

import json
from contextlib import closing
import sqlite3

from collaboration_batches import BatchStore
from collaboration_protocol import MISSING_PLAN, normalize_owner_plan, validate_payload


class LifecycleStore(BatchStore):
    def __init__(self, path, registry=None):
        super().__init__(path, registry)

    def initialize(self, job_id, *, round_limit=20, entry_rounds=1):
        if type(round_limit) is not int or round_limit < 2 or type(entry_rounds) is not int or not 0 <= entry_rounds < round_limit:
            raise ValueError("invalid round budget")
        with self.transaction() as db:
            self._job(db, job_id)
            db.execute("INSERT OR IGNORE INTO collab_lifecycle(job_id,round_limit,used) VALUES (?,?,?)",
                       (job_id, round_limit, entry_rounds))

    def _life(self, db, job_id):
        job = self._job(db, job_id)
        life = db.execute("SELECT * FROM collab_lifecycle WHERE job_id=?", (job_id,)).fetchone()
        if life is None:
            raise ValueError("lifecycle not initialized")
        if job["stopped"]:
            raise ValueError("job stopped")
        return job, life, json.loads(job["plan"])

    def reserve(self, job_id, calls):
        """Atomically budget a batch before transport starts; attempts always cost a round.

        calls: [(call_id, agent_id, role)], role owner/collaborator/final.
        One owner continuation is reserved in capacity calculations, not charged
        until actually requested. Final delivery always retains one slot.
        """
        if not calls or any(not isinstance(c, (list, tuple)) or len(c) != 3 for c in calls):
            raise ValueError("nonempty calls required")
        if any(not isinstance(v, str) or not v.strip() for c in calls for v in c) or len({c[0] for c in calls}) != len(calls):
            raise ValueError("invalid/duplicate call IDs")
        with self.transaction() as db:
            job, life, plan = self._life(db, job_id)
            existing = [db.execute("SELECT agent_id,role FROM collab_calls WHERE job_id=? AND call_id=?", (job_id,c[0])).fetchone() for c in calls]
            if any(existing):
                if not all(existing) or any(tuple(row) != tuple(c[1:]) for row,c in zip(existing,calls)):
                    raise ValueError("call ID reused with different request")
                return False
            roles = {c[2] for c in calls}
            if not roles <= {"owner", "collaborator", "final"}:
                raise ValueError("invalid call role")
            if "owner" in roles and len(calls) != 1:
                raise ValueError("owner continuation must wait for the whole batch; use collaborator role for owner task work")
            for _, agent_id, role in calls:
                self.registry.require(agent_id)
                if role in {"owner", "final"} and agent_id != plan["owner_id" if role == "owner" else "final_responder_id"]:
                    raise ValueError("call identity conflicts with plan")
            if db.execute("SELECT 1 FROM collab_calls WHERE job_id=? AND state='reserved'", (job_id,)).fetchone():
                raise ValueError("previous calls still active")
            if roles == {"final"}:
                if len(calls) != 1 or life["phase"] != "final_ready":
                    raise ValueError("final delivery not ready")
                spare = 0
            else:
                if "final" in roles or life["phase"] != "working" or sum(c[2] == "owner" for c in calls) > 1:
                    raise ValueError("invalid phase or owner count")
                spare = 2 if "collaborator" in roles else (0 if plan["owner_id"] == plan["final_responder_id"] else 1)
            if life["used"] + len(calls) + spare > life["round_limit"]:
                raise ValueError("insufficient budget; enter final delivery using existing material")
            db.executemany("INSERT INTO collab_calls(job_id,call_id,agent_id,role) VALUES (?,?,?,?)",
                           [(job_id,*c) for c in calls])
            db.execute("UPDATE collab_lifecycle SET used=used+?,phase=? WHERE job_id=?",
                       (len(calls), "final_delivery" if "final" in roles else "working", job_id))
            return True

    def accept(self, job_id, call_id, text, payload=None, *, invitation_ids=(), error=None, expected_updates=None):
        """Accept a transport outcome once. Invalid protocols fail, never complete."""
        if not isinstance(text, str):
            raise ValueError("result text required")
        with self.transaction() as db:
            job = self._job(db, job_id)
            if job["stopped"]:
                return False
            _, life, plan = self._life(db, job_id)
            call = db.execute("SELECT * FROM collab_calls WHERE job_id=? AND call_id=?", (job_id,call_id)).fetchone()
            if call is None:
                raise ValueError("unknown call")
            if call["state"] != "reserved":
                return False
            if expected_updates is not None:
                updates = db.execute("SELECT count(*) FROM collab_user_updates WHERE job_id=?", (job_id,)).fetchone()[0]
                if updates != expected_updates:
                    db.execute("UPDATE collab_calls SET state='completed',text=? WHERE job_id=? AND call_id=?", (text,job_id,call_id))
                    return False
            try:
                if error is not None:
                    raise ValueError(str(error))
                validated = validate_payload(payload, registry=self.registry, invitation_ids=invitation_ids,
                                             role=call["role"], phase="final_delivery" if call["role"] == "final" else "collaboration")
                amended = (normalize_owner_plan(validated.get("plan", MISSING_PLAN), plan,
                                               status=validated["status"], registry=self.registry)
                           if call["role"] == "owner" else validated.get("plan", plan))
                if amended != plan:
                    if call["role"] != "owner" or life["phase"] != "working":
                        raise ValueError("only working owner may amend plan")
                    if any(amended[k] != plan[k] for k in ("owner_id", "final_responder_id")):
                        raise ValueError("cannot change roles")
                    old_tasks = {t["task_id"]: t for t in plan["tasks"]}
                    new_tasks = {t["task_id"]: t for t in amended["tasks"]}
                    if any(new_tasks.get(k) != v for k, v in old_tasks.items()):
                        raise ValueError("existing task definitions must not change")
                if any(not any(t["agent_id"] == target for t in amended["tasks"]) for target in invitation_ids):
                    raise ValueError("invited agent has no task")
            except (ValueError, TypeError) as exc:
                db.execute("UPDATE collab_calls SET state='failed',text=? WHERE job_id=? AND call_id=?", (text,job_id,call_id))
                if call["role"] != "collaborator":
                    db.execute("UPDATE collab_lifecycle SET phase='failed',reason=? WHERE job_id=?", (str(exc),job_id))
                return False
            if amended != plan:
                old_ids = {t["task_id"] for t in plan["tasks"]}
                db.executemany("INSERT INTO collab_tasks(job_id,task_id,agent_id,instruction,deps) VALUES (?,?,?,?,?)",
                               [(job_id,t["task_id"],t["agent_id"],t["instruction"],json.dumps(t["depends_on"]))
                                for t in amended["tasks"] if t["task_id"] not in old_ids])
                db.execute("UPDATE collab_jobs SET plan=? WHERE job_id=?", (json.dumps(amended,sort_keys=True,ensure_ascii=False),job_id))
                plan = amended
            if call["role"] == "owner" and validated["status"] == "request_collaboration":
                db.execute("INSERT OR REPLACE INTO collab_authorizations VALUES (?,?)", (job_id,json.dumps(list(invitation_ids))))
            db.execute("UPDATE collab_calls SET state='completed',text=?,payload=? WHERE job_id=? AND call_id=?",
                       (text,json.dumps(validated,ensure_ascii=False),job_id,call_id))
            status = validated["status"]
            if call["role"] == "collaborator":
                # Even the final responder may be a middle-stage collaborator.
                return True
            if call["role"] == "final":
                db.execute("UPDATE collab_lifecycle SET phase='completed',final_text=? WHERE job_id=?", (text,job_id))
            elif status in {"waiting_user", "blocked"}:
                db.execute("UPDATE collab_lifecycle SET phase=?,questions=?,reason=? WHERE job_id=?",
                           (status,json.dumps(validated["questions"],ensure_ascii=False),validated.get("blocked_reason",""),job_id))
            elif status == "completed":
                active = db.execute("SELECT 1 FROM collab_tasks WHERE job_id=? AND state IN ('pending','queued','running')", (job_id,)).fetchone()
                active = active or db.execute("SELECT 1 FROM collab_calls WHERE job_id=? AND state='reserved'", (job_id,)).fetchone()
                if active:
                    db.execute("UPDATE collab_lifecycle SET phase='blocked',reason='unfinished planned tasks' WHERE job_id=?", (job_id,))
                else:
                    same = plan["owner_id"] == plan["final_responder_id"]
                    db.execute("UPDATE collab_lifecycle SET phase=?,final_text=? WHERE job_id=?",
                               ("completed" if same else "final_ready",text if same else "",job_id))
            return True

    def prepare_final(self, job_id, reason):
        """Explicit early/quota delivery: abandon pending work only after active calls end."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("delivery reason required")
        with self.transaction() as db:
            _, life, _ = self._life(db, job_id)
            if life["phase"] not in {"working", "waiting_user", "blocked"}:
                raise ValueError("cannot enter final delivery from current phase")
            if life["used"] >= life["round_limit"]:
                raise ValueError("no final delivery budget")
            if db.execute("SELECT 1 FROM collab_calls WHERE job_id=? AND state='reserved'", (job_id,)).fetchone() or db.execute(
                    "SELECT 1 FROM collab_tasks WHERE job_id=? AND state IN ('queued','running')", (job_id,)).fetchone():
                raise ValueError("active work must stop/finish before delivery")
            db.execute("UPDATE collab_tasks SET state='cancelled' WHERE job_id=? AND state='pending'", (job_id,))
            db.execute("UPDATE collab_lifecycle SET phase='final_ready',reason=? WHERE job_id=?", (reason,job_id))

    def delivery_material(self, job_id):
        with self.transaction() as db:
            job, life, plan = self._life(db, job_id)
            if life["phase"] not in {"final_ready", "final_delivery"}:
                raise ValueError("not in final delivery")
            return dict(request=job["request"], plan=plan, reason=life["reason"],
                        tasks=[dict(r) for r in db.execute("SELECT * FROM collab_tasks WHERE job_id=? ORDER BY task_id", (job_id,))],
                        calls=[dict(r) for r in db.execute("SELECT * FROM collab_calls WHERE job_id=? ORDER BY rowid", (job_id,))])

    def snapshot(self, job_id):
        with self.transaction() as db:
            job = self._job(db, job_id)
            row = db.execute("SELECT * FROM collab_lifecycle WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise ValueError("lifecycle not initialized")
            result = dict(row)
            if job["stopped"]:
                result["phase"] = "stopped"
            return result

    def interrupt_running(self):
        """Explicit startup recovery, not called automatically by each constructor."""
        with self.transaction() as db:
            db.execute("UPDATE collab_lifecycle SET phase='interrupted' WHERE phase IN ('working','final_ready','final_delivery','quiescing')")
            db.execute("UPDATE collab_lifecycle SET phase='interrupted' WHERE job_id IN (SELECT job_id FROM collab_calls WHERE state='reserved')")
            db.execute("UPDATE collab_calls SET state='interrupted' WHERE state='reserved'")
            db.execute("UPDATE collab_tasks SET state='cancelled' WHERE state IN ('queued','running')")
