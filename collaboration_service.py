"""Bounded serial orchestration using the actual workbench transport callback."""

import json
import logging
from dataclasses import dataclass
import threading
import uuid

from collaboration_actions import ActionStore
from collaboration_entry import entry_prompt, parse_entry_response
from collaboration_invitation import terminal_invitation
from collaboration_protocol import MISSING_PLAN, normalize_owner_plan, validate_plan


@dataclass(frozen=True)
class InvocationResult:
    state: str
    text: str
    dispatch_allowed: bool
    message_id: int | None = None

    def __iter__(self):
        # Preserve tuple unpacking for scripted transports and clarification.
        return iter((self.state, self.text))


class DispatchHalted(RuntimeError):
    """The current drive stack must unwind, including call/replan callers."""


class CollaborationService:
    def __init__(self, path, invoke, cancel, *, finalize_message=None):
        self.store = ActionStore(path, finalize_message=finalize_message)
        self.invoke = invoke
        self.cancel = cancel
        self._guard = threading.Lock()
        self._locks = {}
        self._rerun = set()
        self._event_lock = threading.RLock()

    def register_entry(self, job_id, conversation, agent_id, request, payload, raw):
        if payload["status"] == "completed" and not payload.get("plan", {}).get("tasks"):
            raise ValueError("direct entry answer must finish before execution registration")
        if "plan" in payload and any(t["task_id"] == "__clarify_entry__" for t in payload["plan"]["tasks"]):
            raise ValueError("reserved system task ID: __clarify_entry__")
        if "plan" not in payload and payload["status"] not in {"waiting_user", "blocked"}:
            raise ValueError("execution entry requires a plan")
        if "plan" not in payload:
            payload = {**payload, "plan": dict(owner_id=agent_id,final_responder_id=agent_id,tasks=[
                dict(task_id="__clarify_entry__",agent_id=agent_id,instruction="等待澄清后重新理解分工",depends_on=[])])}
        self.store.create(job_id,request,payload["plan"])
        self.store.initialize(job_id)
        self.store.bind(job_id,conversation)
        with self.store.transaction() as db:
            db.execute("INSERT OR IGNORE INTO collab_calls(job_id,call_id,agent_id,role,state,text,payload) VALUES (?,'entry',?,'entry','completed',?,?)",
                       (job_id,agent_id,raw,json.dumps(payload,ensure_ascii=False)))
        self.seed(job_id,agent_id,payload,raw)

    def seed(self, job_id, agent_id, payload, raw):
        targets = terminal_invitation(raw)
        with self.store.transaction() as db:
            if self.store._job(db,job_id)["stopped"]:
                return
            db.execute("INSERT OR REPLACE INTO collab_authorizations VALUES (?,?)", (job_id,json.dumps(targets)))
            if payload["status"] in {"waiting_user","blocked"}:
                db.execute("UPDATE collab_lifecycle SET phase=?,questions=?,reason=? WHERE job_id=?",
                           (payload["status"],json.dumps(payload["questions"],ensure_ascii=False),payload.get("blocked_reason",""),job_id))
            if payload["status"] == "completed":
                db.execute("UPDATE collab_lifecycle SET phase='failed',reason=? WHERE job_id=?",
                           ("Protocol incompatible: planning-only entry; legacy completed preserved, no automatic execution/retry. Verify prior effects.",job_id))

    def action_changed(self, job_id):
        def handle():
            try:
                self.events(job_id)
                self.kick(job_id)
            except Exception as error:
                with self.store.transaction() as db:
                    db.execute("UPDATE collab_lifecycle SET reason=? WHERE job_id=?", (str(error),job_id))
        threading.Thread(target=handle,daemon=True).start()

    def kick(self, job_id):
        with self._guard:
            lock = self._locks.setdefault(job_id, threading.Lock())
        if not lock.acquire(False):
            with self._guard:
                self._rerun.add(job_id)
            return False
        def drive():
            try:
                self.drive(job_id)
            except Exception as error:
                with self.store.transaction() as db:
                    db.execute("UPDATE collab_lifecycle SET phase='failed',reason=? WHERE job_id=? AND phase NOT IN ('completed','quiescing')",
                               (str(error),job_id))
            finally:
                lock.release()
                with self._guard:
                    again = job_id in self._rerun
                    self._rerun.discard(job_id)
                if again:
                    self.kick(job_id)
        threading.Thread(target=drive, daemon=True).start()
        return True

    def events(self, job_id):
        with self._event_lock:
            self._events(job_id)

    def _events(self, job_id):
        with self.store.transaction() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM collab_events WHERE job_id=? AND state='pending' ORDER BY rowid", (job_id,))]
        for event in rows:
            if event["kind"].startswith("cancel_then_"):
                if self.cancel(job_id) is not True:
                    raise DispatchHalted('Cancellation exit/release not confirmed; event remains pending')
                self.store.confirm_quiescent(job_id,event["operation_id"])
            elif event["kind"] == "resume_owner":
                with self.store.transaction() as db:
                    db.execute("UPDATE collab_events SET state='done' WHERE job_id=? AND operation_id=? AND state='pending'", (job_id,event["operation_id"]))

    def control(self, job_id):
        self.events(job_id)
        state = self.store.snapshot(job_id)
        return state["phase"] not in {"stopped", "quiescing", "waiting_user", "blocked", "failed", "interrupted", "completed"}

    def material(self, job_id):
        with self.store.transaction() as db:
            binding = db.execute("SELECT conversation_id FROM collab_bindings WHERE job_id=?", (job_id,)).fetchone()
            if not binding:
                raise ValueError("missing conversation binding")
            tasks = [dict(r) for r in db.execute("SELECT * FROM collab_tasks WHERE job_id=? ORDER BY rowid", (job_id,))]
            calls = [dict(r) for r in db.execute("SELECT * FROM collab_calls WHERE job_id=? ORDER BY rowid", (job_id,))]
        return self.store.view(job_id,binding[0]), tasks, calls

    def append_plan(self, job_id, plan):
        """Owner may add follow-up tasks; existing roles/tasks remain immutable."""
        validate_plan(plan,self.store.registry)
        with self.store.transaction() as db:
            job,life,old = self.store._life(db,job_id)
            if life["phase"] != "working" or any(plan[k] != old[k] for k in ("owner_id","final_responder_id")):
                raise ValueError("cannot change roles or plan in this phase")
            old_tasks = {t["task_id"]: t for t in old["tasks"]}
            new_tasks = {t["task_id"]: t for t in plan["tasks"]}
            if any(new_tasks.get(key) != value for key,value in old_tasks.items()):
                raise ValueError("existing task definitions must not change")
            additions = [t for t in plan["tasks"] if t["task_id"] not in old_tasks]
            db.executemany("INSERT INTO collab_tasks(job_id,task_id,agent_id,instruction,deps) VALUES (?,?,?,?,?)",
                           [(job_id,t["task_id"],t["agent_id"],t["instruction"],json.dumps(t["depends_on"])) for t in additions])
            db.execute("UPDATE collab_jobs SET plan=? WHERE job_id=?", (json.dumps(plan,sort_keys=True,ensure_ascii=False),job_id))

    def _invoke(self, job_id, agent_id, role, prompt, call_id, *, entry_mode=False):
        try:
            kwargs = dict(call_id=call_id)
            if entry_mode:
                kwargs['entry_mode'] = True
            result = self.invoke(job_id, agent_id, role, prompt, **kwargs)
            if isinstance(result, InvocationResult):
                if not result.dispatch_allowed:
                    raise DispatchHalted(result.text or 'Execution not released')
                return result
            # Scripted transports used by protocol tests have no process ownership.
            return result
        except Exception as error:
            try:
                with self.store.transaction() as db:
                    db.execute("UPDATE collab_calls SET state='failed',text=? WHERE job_id=? AND call_id=? AND state='reserved'",
                               (str(error)[:4000], job_id, call_id))
                    db.execute("UPDATE collab_lifecycle SET phase='failed',reason=? WHERE job_id=? AND phase NOT IN ('completed','stopped','quiescing')",
                               (str(error)[:4000], job_id))
            except Exception:
                logging.exception('Could not persist transport failure; dispatch halted')
            raise DispatchHalted(str(error)) from error

    def call(self, job_id, agent_id, role, instruction):
        call_id = uuid.uuid4().hex
        self.store.reserve(job_id,[(call_id,agent_id,role)])
        view,tasks,calls = self.material(job_id)
        update_count = len(view["updates"])
        prompt = (
            "协作调用。只执行当前任务，不自行启动其他Agent。返回可读答复和一个 agent-collaboration 围栏JSON协议块。"
            "协议字段 protocol_version=1,status,handoff,questions；handoff六项为 original_question,response_or_changes,evidence,"
            "rejected_alternatives,uncertainties,expected_action，均为非空文本。无内容写无。默认省略plan；负责人需要新一轮检视时，"
            "可返回仅含新增任务的plan（也兼容完整plan）；遗漏旧任务表示保留，不得修改已有任务和角色。"
            "新增任务可依赖当前计划中的旧任务。完成时省略plan；completed空任务plan也仅表示无更新。协作者和最终答复者不要返回plan。"
            "协作者完成返回completed，不写邀请。负责人可waiting_user并提供questions，或blocked并提供blocked_reason。"
            "负责人有剩余任务返回continue_work，需授权他人则request_collaboration；混合计划允许同时包含自任务和他人任务。"
            "全部任务结束才completed。负责人邀请必须末行独立@名称，任务必须在当前计划中。最终交付只汇总已有材料，不使用修改工具、不再邀请，"
            "材料不足如实披露，状态completed。\n当前角色：" + role + "\n当前任务：" + instruction +
            "\n完整材料：" + json.dumps(dict(view=view,tasks=tasks,calls=calls),ensure_ascii=False))
        invocation = self._invoke(job_id,agent_id,role,prompt,call_id)
        state,text = invocation
        message_id = getattr(invocation, 'message_id', None)
        self.events(job_id)
        try:
            if state != 'completed':
                raise ValueError(state)
            readable,payload = parse_entry_response(text,role=role,phase="final_delivery" if role=="final" else "collaboration")
            if role == "owner":
                # Check the parsed input using the invocation snapshot, but pass
                # the unmerged payload to accept for transactional revalidation.
                normalize_owner_plan(payload.get("plan", MISSING_PLAN), view["plan"],
                                     status=payload["status"], registry=self.store.registry)
        except ValueError as error:
            receipt = self.store.accept_result(job_id,call_id,text,error=str(error),
                        expected_updates=update_count if role=='owner' else None, message_id=message_id)
            return {'status': 'user_update_received'} if receipt.disposition == 'superseded' else None
        receipt = self.store.accept_result(job_id,call_id,text,payload,invitation_ids=terminal_invitation(text),
                                   error=None if state=="completed" else state,
                                   expected_updates=update_count if role=="owner" else None, message_id=message_id)
        if receipt.disposition == 'superseded':
            return {'status': 'user_update_received'}
        return payload if receipt else None

    def drive(self, job_id):
        try:
            return self._drive(job_id)
        except DispatchHalted:
            return

    def _drive(self, job_id):
        decision_used = False
        while self.control(job_id):
            view,tasks,calls = self.material(job_id)
            phase = view["state"]["phase"]
            plan = view["plan"]
            if phase == "final_ready":
                self.call(job_id,plan["final_responder_id"],"final","向用户最终交付结论。说明缺口，保留文件改动不回退。")
                return
            if phase != "working" or any(c["state"] == "reserved" for c in calls):
                return
            if any(t["task_id"] == "__clarify_entry__" and t["state"] == "pending" for t in tasks):
                with self.store.transaction() as db:
                    if not self._clarification_source(db, job_id):
                        db.execute("UPDATE collab_lifecycle SET phase='failed',reason='Unverified clarification placeholder' WHERE job_id=? AND phase='working'", (job_id,))
                        return
                self.replan(job_id,view)
                continue
            pending = [t for t in tasks if t["state"] == "pending"]
            done = {t["task_id"] for t in tasks if t["state"] == "completed"}
            ready = [t for t in pending if set(json.loads(t["deps"])) <= done]
            # Latest owner invitation authorizes only those collaborator tasks.
            with self.store.transaction() as db:
                auth = db.execute("SELECT targets FROM collab_authorizations WHERE job_id=?", (job_id,)).fetchone()
            targets = set(json.loads(auth[0])) if auth else set()
            ready = [t for t in ready if t["agent_id"] == plan["owner_id"] or t["agent_id"] in targets]
            if ready:
                decision_used = False
                task = ready[0]  # conservative serialization in shared workspace
                try:
                    result = self.call(job_id,task["agent_id"],"collaborator",task["instruction"])
                except ValueError as error:
                    if "budget" not in str(error):
                        raise
                    self.store.prepare_final(job_id,"额度不足，依据已有材料收尾")
                    continue
                if not self.control(job_id):
                    return
                with self.store.transaction() as db:
                    db.execute("UPDATE collab_tasks SET state=?,result=? WHERE job_id=? AND task_id=? AND state='pending'",
                               ("completed" if result and result["status"]=="completed" else "failed",json.dumps(result,ensure_ascii=False),job_id,task["task_id"]))
                continue
            if any(c["state"] == "reserved" for c in calls) or any(t["state"] in {"queued", "running"} for t in tasks):
                return  # Completion/event callback kicks the job; waiting is not failure.
            if decision_used:
                with self.store.transaction() as db:
                    db.execute("UPDATE collab_lifecycle SET phase='blocked',reason=? WHERE job_id=? AND phase='working'",
                               ("No runnable work after one owner decision; check failed dependencies or missing authorization.",job_id))
                return
            decision_used = True
            try:
                result = self.call(job_id,plan["owner_id"],"owner","Review results. Remaining self tasks: continue_work; authorize others: request_collaboration plus terminal invitation. Complete only after tasks finish; otherwise explain blocked_reason.")
            except ValueError as error:
                if "budget" not in str(error):
                    raise
                self.store.prepare_final(job_id,"Budget exhausted; deliver existing material")
                continue
            if result and result["status"] == "user_update_received":
                decision_used = False
                continue
            if not result or not self.control(job_id):
                return

    def _clarification_source(self, db, job_id):
        """Verify system provenance from the original unmodified entry text."""
        job = self.store._job(db, job_id)
        plan = json.loads(job["plan"])
        tasks = list(db.execute("SELECT * FROM collab_tasks WHERE job_id=?", (job_id,)))
        entry = db.execute("SELECT text,agent_id FROM collab_calls WHERE job_id=? AND call_id='entry' AND role='entry'", (job_id,)).fetchone()
        if not entry or len(tasks) != 1 or len(plan["tasks"]) != 1:
            return False
        try:
            _, original = parse_entry_response(entry["text"])
        except (ValueError, TypeError):
            return False
        expected = dict(task_id="__clarify_entry__",agent_id=entry["agent_id"],instruction="等待澄清后重新理解分工",depends_on=[])
        task = tasks[0]
        return (original["status"] in {"waiting_user", "blocked"} and "plan" not in original
                and plan["owner_id"] == entry["agent_id"] and plan["tasks"] == [expected]
                and task["task_id"] == expected["task_id"] and task["agent_id"] == expected["agent_id"]
                and task["instruction"] == expected["instruction"] and json.loads(task["deps"]) == []
                and task["state"] == "pending" and task["batch_id"] is None
                and not db.execute("SELECT 1 FROM collab_calls WHERE job_id=? AND role='collaborator'", (job_id,)).fetchone()
                and not db.execute("SELECT 1 FROM collab_batches WHERE job_id=?", (job_id,)).fetchone())

    def replan(self, job_id, view):
        agent_id = view["plan"]["owner_id"]
        call_id = uuid.uuid4().hex
        update_count = len(view["updates"])
        try:
            self.store.reserve(job_id,[(call_id,agent_id,"owner")])
        except ValueError as error:
            if "budget" not in str(error):
                raise
            self.store.prepare_final(job_id,"澄清额度用尽；尚未确定完整分工")
            return
        request = view["request"] + "\n用户补充：\n" + "\n".join(u["content"] for u in view["updates"])
        invocation = self._invoke(job_id,agent_id,"owner",entry_prompt(request),call_id,entry_mode=True)
        state,raw = invocation
        message_id = getattr(invocation, 'message_id', None)
        self.events(job_id)
        if self.store.snapshot(job_id)["phase"] in {"stopped","quiescing","final_ready"}:
            self.store.accept_result(job_id,call_id,raw,error='Invocation cancelled',message_id=message_id)
            return
        try:
            _,payload = parse_entry_response(raw)
            if state != "completed":
                raise ValueError(state)
            if payload["status"] == "completed" and payload["plan"]["owner_id"] != agent_id:
                raise ValueError("direct clarification answer cannot change owner")
        except ValueError as error:
            self.store.accept_result(job_id,call_id,raw,error=str(error),
                                     expected_updates=update_count,message_id=message_id)
            return
        with self.store.transaction() as db:
            def finalize(disposition, reason=''):
                return self.store.finalize(db, message_id=message_id, job_id=job_id,
                              call_id=call_id, disposition=disposition, reason=reason)
            job = self.store._job(db, job_id)
            life = db.execute("SELECT * FROM collab_lifecycle WHERE job_id=?", (job_id,)).fetchone()
            call = db.execute("SELECT * FROM collab_calls WHERE job_id=? AND call_id=?", (job_id,call_id)).fetchone()
            if job["stopped"] or life["phase"] != "working" or not call or call["state"] != "reserved":
                finalize('cancelled' if job['stopped'] or (call and call['state']=='cancelled') else 'superseded',
                         'Clarification is no longer current')
                return
            if finalize('inspect') == 'cancelled':
                db.execute("UPDATE collab_calls SET state='cancelled' WHERE job_id=? AND call_id=?", (job_id,call_id))
                return
            if db.execute("SELECT 1 FROM collab_calls WHERE job_id=? AND state='reserved' AND call_id!=?", (job_id,call_id)).fetchone():
                finalize('superseded', 'Another invocation is reserved')
                return
            current_count = db.execute("SELECT count(*) FROM collab_user_updates WHERE job_id=?", (job_id,)).fetchone()[0]
            if current_count != update_count:
                db.execute("UPDATE collab_calls SET state='completed',text=? WHERE job_id=? AND call_id=?", (raw,job_id,call_id))
                finalize('superseded', 'User update superseded this reply')
                return  # drive obtains a fresh view; stale answers cannot finish the job.
            if not self._clarification_source(db, job_id):
                db.execute("UPDATE collab_calls SET state='failed',text=? WHERE job_id=? AND call_id=?", (raw,job_id,call_id))
                db.execute("UPDATE collab_lifecycle SET phase='failed',reason='Unverified clarification placeholder' WHERE job_id=?", (job_id,))
                finalize('rejected', 'Unverified clarification placeholder')
                return
            db.execute("UPDATE collab_calls SET state='completed',text=?,payload=? WHERE job_id=? AND call_id=? AND state='reserved'",
                       (raw,json.dumps(payload,ensure_ascii=False),job_id,call_id))
            finalize('accepted')
            if payload["status"] == "completed":
                plan = json.loads(job["plan"])
                plan["final_responder_id"] = payload["plan"]["final_responder_id"]
                db.execute("UPDATE collab_jobs SET plan=? WHERE job_id=?", (json.dumps(plan,sort_keys=True,ensure_ascii=False),job_id))
                db.execute("UPDATE collab_tasks SET state='completed',result=? WHERE job_id=? AND task_id='__clarify_entry__' AND state='pending'",
                           ("澄清已解决\n" + payload["handoff"]["response_or_changes"],job_id))
                same = plan["owner_id"] == plan["final_responder_id"]
                db.execute("UPDATE collab_lifecycle SET phase=?,final_text=?,questions='[]',reason='' WHERE job_id=? AND phase='working'",
                           ("completed" if same else "final_ready",raw if same else "",job_id))
                return
            if "plan" in payload:
                plan = payload["plan"]
                db.execute("UPDATE collab_jobs SET plan=? WHERE job_id=?", (json.dumps(plan,sort_keys=True,ensure_ascii=False),job_id))
                db.execute("DELETE FROM collab_tasks WHERE job_id=?", (job_id,))
                db.executemany("INSERT INTO collab_tasks(job_id,task_id,agent_id,instruction,deps) VALUES (?,?,?,?,?)",
                               [(job_id,t["task_id"],t["agent_id"],t["instruction"],json.dumps(t["depends_on"])) for t in plan["tasks"]])
            # Keep call result, replacement plan, authorization and lifecycle atomic.
            db.execute("INSERT OR REPLACE INTO collab_authorizations VALUES (?,?)", (job_id,json.dumps(terminal_invitation(raw))))
            if payload["status"] in {"waiting_user", "blocked"}:
                db.execute("UPDATE collab_lifecycle SET phase=?,questions=?,reason=? WHERE job_id=?",
                           (payload["status"],json.dumps(payload["questions"],ensure_ascii=False),payload.get("blocked_reason",""),job_id))
