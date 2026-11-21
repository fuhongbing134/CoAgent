"""Execution and cancellation for the Web workbench."""

import hashlib
import logging
import os
import shutil
import signal
import sqlite3
import subprocess
import threading
from contextlib import closing
from pathlib import Path

from workbench.execution.execution_runtime import Execution
from workbench.execution.execution_runtime import ExecutionAdmissionError
from workbench.execution.execution_runtime import format_execution_error
from workbench.execution.execution_tracking import DomainBusy


class AgentRunCancelled(Exception):
    """Raised when the user stops an active Agent CLI process."""


def active_run(message_id, *, ACTIVE_RUNS, ACTIVE_RUNS_LOCK):
    # Never retain the registry lock when taking a per-run lock or entering DB.
    with ACTIVE_RUNS_LOCK:
        return ACTIVE_RUNS.get(message_id)


def begin_execution(message_id, *, source_kind, job_id=None, call_id=None, DB_PATH, SERVICE_INSTANCE_ID, active_run, resolve_working_directory):
    run = active_run(message_id)
    if not run:
        raise RuntimeError('Missing active execution')
    with run['state_lock']:
        if run['claimed']:
            raise RuntimeError('Duplicate execution entry')
        run['claimed'] = True
        if run['cancel_requested']:
            raise AgentRunCancelled()
        try:
            directory = resolve_working_directory(run['conversation_id'])
            run['execution'] = Execution(DB_PATH, instance_id=SERVICE_INSTANCE_ID,
                agent_id=run['agent_id'], conversation_id=run['conversation_id'],
                working_directory=directory, source_kind=source_kind, message_id=message_id,
                job_id=job_id, call_id=call_id)
        except DomainBusy:
            # DomainBusy is an established admission contract; keep it catchable.
            raise
        except Exception as error:
            # Assignment only happens after successful reservation; no tokenless finish.
            raise ExecutionAdmissionError(error) from error
        return run['execution']


def finish_execution(message_id, *, outcome=None, active_run, connect_workbench):
    run = active_run(message_id)
    if not run or not run.get('execution'):
        return False
    execution = run['execution']
    try:
        row = None
        status_error = None
        try:
            if outcome is None:
                with closing(connect_workbench()) as db:
                    row = db.execute('SELECT status FROM workbench_messages WHERE id=?', (message_id,)).fetchone()
        except Exception as error:
            # A failed message read must not bypass not_started finalization.
            status_error = error
        outcome = ('failed' if run.get('failed') else outcome if outcome is not None else
                   row[0] if row and row[0] in {'completed', 'cancelled'} else 'failed')
        if not execution.finish(outcome):
            raise RuntimeError('退出未确认；保留执行域占用，已停止后续派发，需人工核验恢复。')
        if status_error is not None:
            raise ExecutionAdmissionError(status_error) from status_error
        if execution.dispatch_error:
            # A known unstarted/finished process may release its domain, while a
            # session conflict still stops this collaboration drive (no replay).
            return False
        return True
    except Exception as error:
        logging.exception('Execution release failed for message %s', message_id)
        # A release fault is visible even when a reply was already written.
        try:
            notice = ('\n[本次执行已收尾，但状态读取失败，停止派发] ' if execution.released else
                      '\n[执行域未释放，停止派发；需人工核验] ')
            with closing(connect_workbench()) as db, db:
                db.execute("UPDATE workbench_messages SET status=CASE WHEN status='cancelled' THEN status ELSE 'failed' END,content=content || ? WHERE id=?",
                           (notice + format_execution_error(error)[:1000], message_id))
        except Exception:
            logging.exception('Could not persist execution release fault')
        return False


def register_active_run(message_id: int, conversation_id: str, agent_id: str, *, ACTIVE_RUNS, ACTIVE_RUNS_LOCK, utc_now) -> None:
    with ACTIVE_RUNS_LOCK:
        if message_id in ACTIVE_RUNS:
            return
        ACTIVE_RUNS[message_id] = {
            "message_id": message_id,
            "conversation_id": conversation_id,
            "agent_id": agent_id,
            "process": None,
            "state_lock": threading.RLock(),
            "claimed": False,
            "execution": None,
            "cancel_requested": False,
            "started_at": utc_now(),
        }


def unregister_active_run(message_id: int, *, ACTIVE_RUNS, ACTIVE_RUNS_LOCK) -> None:
    with ACTIVE_RUNS_LOCK:
        ACTIVE_RUNS.pop(message_id, None)


def is_run_cancelled(message_id: int, *, ACTIVE_RUNS, ACTIVE_RUNS_LOCK) -> bool:
    with ACTIVE_RUNS_LOCK:
        run = ACTIVE_RUNS.get(message_id)
        return bool(run and run["cancel_requested"])


def list_active_runs(*, ACTIVE_RUNS, ACTIVE_RUNS_LOCK) -> list[dict]:
    with ACTIVE_RUNS_LOCK:
        return [
            {key: run[key] for key in ("message_id", "conversation_id", "agent_id", "started_at")}
            for run in ACTIVE_RUNS.values()
            if not run["cancel_requested"]
        ]


def terminate_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        taskkill = shutil.which("taskkill")
        if taskkill:
            try:
                subprocess.run(
                    [taskkill, "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=8,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        try:
            process.terminate()
        except OSError:
            return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        try:
            process.kill()
        except OSError:
            pass


def stop_active_run(message_id: int, *, active_run, update_workbench_message) -> bool:
    run = active_run(message_id)
    if not run:
        return False
    with run['state_lock']:
        execution = run.get('execution')
        if execution and execution.done.is_set():
            return False
        run['cancel_requested'] = True
        try:
            if execution:
                execution.cancel()
        except Exception:
            logging.exception('Cancel intent persistence failed; process cancellation remains active')
    update_workbench_message(message_id, '已停止。', 'cancelled')
    return True


def stop_all_active_runs(*, ACTIVE_RUNS, ACTIVE_RUNS_LOCK, stop_active_run) -> int:
    with ACTIVE_RUNS_LOCK:
        message_ids = [
            message_id for message_id, run in ACTIVE_RUNS.items()
            if not run["cancel_requested"]
        ]
    stopped = 0
    for message_id in message_ids:
        try:
            if stop_active_run(message_id):
                stopped += 1
        except sqlite3.Error:
            # Cancellation has already been signalled before persistence errors.
            stopped += 1
    return stopped


def prepare_execution_session(message_id, agent_id, executable, environment, *, DSH_SESSION_KEY, GROK_SESSION_KEY, SESSION_CONFIGURATION_RESOLVER, active_run):
    """One-way authority selection, frozen for this invocation. Never dual-write."""
    execution = active_run(message_id)['execution']
    resolver = SESSION_CONFIGURATION_RESOLVER
    if resolver is None:
        execution.session_configuration = None
        if agent_id == 'grok':
            return execution.select_legacy_session('grok', GROK_SESSION_KEY)
        return execution.select_legacy_session('codex' if agent_id == 'codex' else 'dsh',
                                               None if agent_id == 'codex' else DSH_SESSION_KEY)
    try:
        config = resolver(agent_id, execution.working_directory, executable, environment)
        if config is None or not config.evidence_reference:
            raise RuntimeError('Session namespace evidence missing; refusing production cutover')
        expected_adapter = 'codex' if agent_id == 'codex' else 'grok' if agent_id == 'grok' else 'dsh'
        if config.identity.adapter_id != expected_adapter:
            raise RuntimeError('Session adapter mismatch')
    except Exception as error:
        failure = ExecutionAdmissionError(error)
        execution.dispatch_error = str(failure)
        raise failure from error
    execution.session_configuration = config
    return execution.claim_session(config.identity)


def run_managed_process(message_id: int, command: list[str], active_run, **kwargs) -> subprocess.CompletedProcess:
    run = active_run(message_id)
    if not run or not run.get('execution'):
        raise RuntimeError('CLI requires an admitted execution domain')
    execution = run['execution']
    metadata = kwargs.pop('diagnostic_metadata', None) or {}
    kwargs.pop('text', None)
    kwargs.pop('encoding', None)
    kwargs.pop('errors', None)
    if kwargs.pop('shell', False):
        raise ValueError('Managed execution does not allow shell=True')
    command = list(command)
    if not Path(command[0]).is_absolute():
        resolved = shutil.which(command[0])
        if not resolved:
            raise FileNotFoundError('Launch executable not found')
        command[0] = resolved
    snapshot = dict(agent_id=run['agent_id'], executable=command[0],
                    working_directory=str(execution.working_directory), workspace_version=0,
                    launch_mode=metadata.get('session_mode', 'resume' if '--session-id' in command or 'resume' in command else 'new'))
    if run['agent_id'] == 'codex':
        snapshot['sandbox'] = command[command.index('--sandbox') + 1]
    elif run['agent_id'] == 'grok':
        snapshot['permission_mode'] = command[command.index('--permission-mode') + 1]
        snapshot['tools_denied'] = '--deny' in command and command[command.index('--deny') + 1] == '*'
    else:
        snapshot['profile'] = command[command.index('--profile') + 1]
        patch = command[command.index('--patch') + 1]
        snapshot['configuration_id'] = hashlib.sha256(patch.encode('utf-8')).hexdigest()
    config = getattr(execution, 'session_configuration', None)
    if config is not None:
        try:
            config.check_launch(command, kwargs.get('cwd'), kwargs.get('env', os.environ))
        except Exception:
            execution.dispatch_error = 'Resolved backend configuration changed before startup'
            raise
        snapshot['configuration_id'] = config.identity.config_fingerprint
        snapshot['session_identity'] = dict(zip(
            ('adapter_id','backend_scope','config_fingerprint','workspace_id','workspace_version'),
            config.identity.values()))
    result = execution.launch(command, snapshot=snapshot, **kwargs)
    if config is not None:
        validation = execution.observe_session(config.identity.adapter_id,
            result.stdout.decode('utf-8', errors='replace'),
            normal_completion=result.outcome == 'completed' and result.returncode == 0)
        try:
            config.check_launch(command, kwargs.get('cwd'), kwargs.get('env', os.environ))
        except Exception:
            from workbench.execution.execution_tracking import SessionValidation
            execution.session_validation = SessionValidation('unverified', validation.actual_id, False, validation.observed_ids)
            execution.dispatch_error = 'Resolved backend configuration changed during execution'
            raise
        if result.outcome == 'completed' and validation.status != 'verified':
            execution.dispatch_error = 'Session ID missing or inconsistent'
            raise RuntimeError('Session ID missing or inconsistent; binding retained for review')
    if result.outcome == 'cancelled':
        raise AgentRunCancelled()
    if result.outcome == 'timeout':
        raise subprocess.TimeoutExpired(command, kwargs.get('timeout'))
    if result.outcome != 'completed' or result.exit_evidence != 'confirmed':
        raise RuntimeError('Managed process failed: ' + (result.error_type or result.outcome))
    return subprocess.CompletedProcess(command, result.returncode,
        result.stdout.decode('utf-8', errors='replace'), result.stderr.decode('utf-8', errors='replace'))
