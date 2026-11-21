"""Dispatch and finalize workbench tasks."""

from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import closing

from workbench.agents.agent_registry import default_registry
from workbench.collaboration.collaboration_entry import entry_prompt
from workbench.collaboration.collaboration_entry import parse_entry_response
from workbench.collaboration.collaboration_entry import route_message
from workbench.collaboration.collaboration_service import InvocationResult
from workbench.execution.execution_runtime import format_execution_error
from workbench.workers.result import WorkerResult


def cancel_collaboration(
    job_id,
    *,
    ACTIVE_RUNS,
    ACTIVE_RUNS_LOCK,
    COLLAB_MESSAGES,
    connect_workbench,
    stop_active_run,
):
    with ACTIVE_RUNS_LOCK:
        runs = [ACTIVE_RUNS[i] for i in COLLAB_MESSAGES.get(job_id, ()) if i in ACTIVE_RUNS]
    for run in runs:
        stop_active_run(run['message_id'])
    deadline = time.monotonic() + 12
    for run in runs:
        execution = run.get('execution')
        if execution and (not execution.done.wait(max(0, deadline - time.monotonic()))
                          or not execution.released):
            return False
    # Also include runs whose registry entry disappeared, e.g. a release failure.
    with closing(connect_workbench()) as db:
        return not db.execute("SELECT 1 FROM execution_runs WHERE source_job_id=? AND state IN ('pending','running','quiescing')", (job_id,)).fetchone()


def invoke_collaboration(job_id, agent_id, role, prompt, *, call_id, entry_mode=False,
    ACTIVE_RUNS_LOCK,
    COLLAB_AGENT_LOCKS,
    COLLAB_CONTEXT,
    COLLAB_MESSAGES,
    active_run,
    agent_worker,
    begin_execution,
    collaboration_service,
    connect_workbench,
    finish_execution,
    register_active_run,
    unregister_active_run,
    update_workbench_message,
    utc_now,
):
    service = collaboration_service()
    with service.store.transaction() as db:
        call = db.execute("SELECT state,text FROM collab_calls WHERE job_id=? AND call_id=? AND agent_id=? AND role=?",
                          (job_id,call_id,agent_id,role)).fetchone()
        if not call:
            raise ValueError('Missing real collaboration call')
        if call['state'] != 'reserved':
            return InvocationResult(call['state'], call['text'] or '', True)
        conversation = db.execute("SELECT conversation_id FROM collab_bindings WHERE job_id=?", (job_id,)).fetchone()[0]
    with COLLAB_AGENT_LOCKS[agent_id]:
        # Recheck after serial-agent admission: another invocation may have
        # finished while this caller waited, before its call row was accepted.
        with closing(connect_workbench()) as db:
            previous = db.execute("""SELECT r.state,r.source_message_id FROM execution_runs r
                WHERE r.source_job_id=? AND r.source_call_id=? AND r.agent_id=?
                LIMIT 1""", (job_id,call_id,agent_id)).fetchone()
            if previous:
                # Recover the original reply for reception, not a placeholder
                # that would be parsed as a new invalid protocol response.
                message = db.execute('SELECT content FROM workbench_messages WHERE id=?',
                                     (previous[1],)).fetchone()
        if previous:
            state = previous[0]
            text = message[0] if message and message[0] else '重复调用，不重放；原回复不可用，需人工核查。'
            return InvocationResult(state, text,
                                    state in {'completed','failed','cancelled'}, previous[1])
        if service.store.snapshot(job_id)['phase'] in {'stopped', 'quiescing'}:
            return InvocationResult('cancelled', 'Cancelled before CLI start', False)
        with closing(connect_workbench()) as db, db:
            message_id = db.execute("INSERT INTO workbench_messages(conversation_id,sender_id,recipient_id,content,created_at,status) VALUES (?,?,'user','',?,'pending')",
                                    (conversation,agent_id,utc_now())).lastrowid
        register_active_run(message_id,conversation,agent_id)
        active_run(message_id)['collaboration'] = (job_id, call_id)
        with ACTIVE_RUNS_LOCK:
            COLLAB_MESSAGES.setdefault(job_id,set()).add(message_id)
        allowed = False
        reply = WorkerResult('cancelled', 'Cancelled before CLI start')
        previous_final = getattr(COLLAB_CONTEXT, 'final_delivery', False)
        try:
            if service.store.snapshot(job_id)['phase'] not in {'working', 'final_delivery'}:
                update_workbench_message(message_id, '已停止。', 'cancelled')
            else:
                begin_execution(message_id, source_kind='collaboration', job_id=job_id, call_id=call_id)
                COLLAB_CONTEXT.final_delivery = role == 'final'
                worker = agent_worker(agent_id)
                reply = worker(conversation,prompt,message_id,message_id)
                if not isinstance(reply, WorkerResult):
                    raise RuntimeError('Worker returned no structured result')
        except Exception as error:
            active_run(message_id)['failed'] = True
            reply = WorkerResult('failed', '执行失败：' + format_execution_error(error)[:2000])
            update_workbench_message(message_id, reply.text, 'failed')
        finally:
            COLLAB_CONTEXT.final_delivery = previous_final
            allowed = finish_execution(message_id, outcome=reply.status)
            unregister_active_run(message_id)
            with ACTIVE_RUNS_LOCK:
                COLLAB_MESSAGES.get(job_id,set()).discard(message_id)
        return InvocationResult(reply.status, reply.text, allowed, message_id)


def worker_reply(
    message_id, text, status,
    *,
    active_run,
    stage_collaboration_reply,
    update_workbench_message,
):
    run = active_run(message_id)
    execution = run.get('execution') if run else None
    result = WorkerResult(status, text, execution.result if execution else None,
                          execution.session_validation if execution else None)
    if status == 'completed' and run and run.get('collaboration'):
        stage_collaboration_reply(message_id, *run['collaboration'], text)
    else:
        update_workbench_message(message_id, text, status)
    return result


def agent_worker(
    agent_id: str,
    *,
    run_codex,
    run_deepseek_harness,
    run_grok,
):
    adapter = default_registry().require(agent_id).adapter
    return {'codex': run_codex, 'dsh': run_deepseek_harness, 'grok': run_grok}[adapter]


def run_agent_task(
    agent_id: str,
    conversation_id: str,
    prompt: str,
    assistant_message_id: int,
    user_message_id: int,
    *,
    COLLAB_CONTEXT,
    active_run,
    agent_worker,
    begin_execution,
    collaboration_service,
    connect_workbench,
    fail_entry_message,
    finish_execution,
    is_run_cancelled,
    unregister_active_run,
) -> None:
    previous_validation = getattr(COLLAB_CONTEXT, "validating_entry", False)
    previous_received = getattr(COLLAB_CONTEXT, "entry_reply_received", False)
    owns_execution = False
    pending_kick = None
    try:
        _recipients, collaboration = route_message(prompt)
        COLLAB_CONTEXT.validating_entry = collaboration
        COLLAB_CONTEXT.entry_reply_received = False
        if collaboration:
            with closing(connect_workbench(timeout=10)) as connection:
                if connection.execute("SELECT 1 FROM collaboration_entry_results WHERE message_id=?", (assistant_message_id,)).fetchone():
                    return  # Already processed: never invoke the Agent again.
                row = connection.execute("SELECT status FROM workbench_messages WHERE id=?", (assistant_message_id,)).fetchone()
                if not row or row[0] != "pending":
                    return
        with closing(connect_workbench()) as check:
            row = check.execute('SELECT status FROM workbench_messages WHERE id=?', (assistant_message_id,)).fetchone()
            if not row or row[0] != 'pending':
                return
        run = active_run(assistant_message_id)
        if not run:
            return  # A concurrent completion may already have unregistered it.
        with run['state_lock']:
            if run['claimed']:
                return
            owns_execution = True
            begin_execution(assistant_message_id, source_kind='collaboration' if collaboration else 'workbench',
                            job_id=f'entry-{assistant_message_id}' if collaboration else None,
                            call_id='entry' if collaboration else None)
        worker = agent_worker(agent_id)
        reply = worker(conversation_id, entry_prompt(prompt) if collaboration else prompt,
               assistant_message_id, user_message_id)
        if not isinstance(reply, WorkerResult):
            raise RuntimeError("Worker returned no structured result")
        if collaboration:
            # Persist validated entry material; scheduling is a later stage.
            with closing(connect_workbench(timeout=10)) as connection:
                row = connection.execute(
                    "SELECT content, status FROM workbench_messages WHERE id = ?",
                    (assistant_message_id,),
                ).fetchone()
            if row and row[1] == "pending" and COLLAB_CONTEXT.entry_reply_received:
                try:
                    _readable, payload = parse_entry_response(row[0])
                except ValueError as error:
                    fail_entry_message(assistant_message_id, "protocol incompatible", error)
                else:
                    # Stop and commit share this lock; DB state is checked under
                    # the write transaction as well. A late result cannot undo stop.
                    with run['state_lock']:
                        if run["cancel_requested"]:
                            return
                        with closing(connect_workbench(timeout=10)) as connection, connection:
                            connection.execute("BEGIN IMMEDIATE")
                            updated = connection.execute("UPDATE workbench_messages SET status='completed' WHERE id=? AND status='pending'",
                                                         (assistant_message_id,)).rowcount
                            if not updated:
                                return
                            connection.execute("INSERT INTO collaboration_entry_results VALUES (?, ?, ?)",
                                (assistant_message_id, conversation_id, json.dumps(payload, ensure_ascii=False)))
                    if payload["status"] == "completed" and payload["plan"]["tasks"] == []:
                        return
                    job_id = f"entry-{assistant_message_id}"
                    service = None
                    try:
                        service = collaboration_service()
                        service.register_entry(job_id,conversation_id,agent_id,prompt,payload,row[0])
                        if is_run_cancelled(assistant_message_id):
                            service.store.stop(job_id)
                        else:
                            pending_kick = (service, job_id)
                    except Exception as error:
                        if service is not None:
                            try:
                                with service.store.transaction() as db:
                                    db.execute("UPDATE collab_lifecycle SET phase='failed',reason=? WHERE job_id=? AND phase NOT IN ('completed','quiescing')",
                                               ("registration/dispatch failed: " + str(error),job_id))
                            except Exception:
                                logging.exception("Could not persist collaboration registration failure")
                        fail_entry_message(assistant_message_id, "registration/dispatch failed", error)
            elif row and row[1] == "pending":
                fail_entry_message(assistant_message_id, "transport failed", "worker returned without a successful reply")
    except Exception as error:
        fail_entry_message(assistant_message_id, "persistence failed", error)
    finally:
        COLLAB_CONTEXT.validating_entry = previous_validation
        COLLAB_CONTEXT.entry_reply_received = previous_received
        if owns_execution:
            allowed = finish_execution(assistant_message_id)
            unregister_active_run(assistant_message_id)
            if pending_kick:
                service, job_id = pending_kick
                if allowed and not run['cancel_requested']:
                    service.kick(job_id)
                else:
                    with service.store.transaction() as db:
                        db.execute("UPDATE collab_lifecycle SET phase='failed',reason='Execution domain not released' WHERE job_id=? AND phase NOT IN ('completed','quiescing','stopped')", (job_id,))


def fail_entry_message(
    message_id, stage, error,
    *,
    active_run,
    connect_workbench,
):
    """Preserve the Agent's raw reply and never overwrite cancellation."""
    logging.error("Entry %s: %s: %s", message_id, stage, error)
    try:
        run = active_run(message_id)
        lock = run['state_lock'] if run else threading.RLock()
        with lock:
            if run and run['cancel_requested']:
                return
            with closing(connect_workbench(timeout=10)) as db, db:
                db.execute("UPDATE workbench_messages SET status='failed',content=content || ? WHERE id=? AND status IN ('pending','completed')",
                           ("\n\n[Collaboration " + stage + "] " + format_execution_error(error)[:4000],message_id))
    except Exception:
        logging.exception("Could not persist entry failure for message %s", message_id)
