"""One execution's startup/cancel/session/finish boundary; no registry mutation.

Lock order: snapshot ACTIVE_RUNS under its lock and RELEASE it before entering
this object's lock. Here: per-run lock -> MigrationBarrier -> BEGIN IMMEDIATE.
DB transactions never call registry/runtime callbacks. Process waits hold none
of these locks. ManagedProcess's private resume lock only guards memory/resume.
"""
import threading
import sqlite3

from execution_inspection import InspectionQueryError
from execution_tracking import (DomainBusy, ExecutionTracker, SessionReviewRequired,
                                SessionValidation, StaleRun)
from managed_process import ManagedProcess


def execution_error_details(error, *, admission=False):
    """Safe admission diagnostics; never infer concurrency from a PID/instance alone."""
    category = getattr(error, 'category', None)
    reason = getattr(error, 'reason_code', None)
    if isinstance(error, StaleRun):
        category, reason = 'occupancy_blocked', 'ownership_inconsistent'
    elif isinstance(error, (InspectionQueryError, sqlite3.Error)):
        category, reason = 'query_failed', 'query_failed'
    elif isinstance(error, DomainBusy):
        category = 'concurrent_rejection' if category == 'concurrent_rejection' else 'occupancy_blocked'
    if category not in {'occupancy_blocked', 'concurrent_rejection', 'query_failed'}:
        if not admission:
            return None
        category, reason = 'query_failed', 'admission_unavailable'
    if not isinstance(reason, str) or not reason or not all(c in 'abcdefghijklmnopqrstuvwxyz_0123456789' for c in reason):
        reason = 'admission_unavailable'
    return dict(category=category, reason_code=reason)


def format_execution_error(error):
    details = execution_error_details(error)
    if details is None:
        return str(error).strip() or error.__class__.__name__
    labels = dict(occupancy_blocked='旧占用阻断', concurrent_rejection='本次并发拒绝',
                  query_failed='查询失败')
    explanations = dict(occupancy_blocked='关联占用或归属记录需核验，已停止后续派发。',
                        concurrent_rejection='检测到本次并发关联占用，本次不启动；未自动重试。',
                        query_failed='无法完成状态读取或准入核对，停止后续派发。')
    if details['category'] == 'occupancy_blocked':
        if details['reason_code'] == 'domain_occupied_active':
            labels['occupancy_blocked'] = '正在忙'
            explanations['occupancy_blocked'] = '该 agent 正在忙另一个任务，请等它完成后再试。'
        elif details['reason_code'] == 'domain_occupied_retained':
            explanations['occupancy_blocked'] = '存在旧执行占用，退出情况未知，需先处理遗留占用。'
    return f"[{labels[details['category']]}] {explanations[details['category']]} ({details['reason_code']})"


class ExecutionAdmissionError(RuntimeError):
    """Sanitized selection/configuration failure; original cause stays in traceback."""
    def __init__(self, error):
        details = execution_error_details(error, admission=True)
        self.category = details['category']
        self.reason_code = details['reason_code']
        super().__init__(format_execution_error(self))


class Execution:
    def __init__(self, path, *, instance_id, agent_id, conversation_id,
                 working_directory, source_kind, message_id, job_id=None, call_id=None):
        if not instance_id:
            raise RuntimeError('Execution service identity missing; start through startup_server')
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.done = threading.Event()
        self.released = False
        self.result = None
        self.process = None
        self.session_claim = None
        self.session_selected = False
        self.selected_session_id = None
        self.session_validation = SessionValidation()
        self.dispatch_error = None
        self.working_directory = working_directory
        self.tracker = ExecutionTracker(path)
        self.token = self.tracker.begin_run(
            agent_id=agent_id, conversation_id=conversation_id, instance_id=instance_id,
            source_kind=source_kind, source_job_id=job_id, source_call_id=call_id,
            source_message_id=message_id, config_snapshot={'working_directory': str(working_directory)},
            workspace_id=str(working_directory), workspace_version=0)

    def claim_session(self, identity, **kwargs):
        with self.lock:
            try:
                self._check_session_selection()
                self.session_claim = self.tracker.claim_session(self.token, identity, **kwargs)
                self.selected_session_id = self.session_claim.expected_id
                self.session_selected = True
            except SessionReviewRequired as error:
                # Preserve task-5 review semantics and its distinct user guidance.
                self.dispatch_error = str(error)
                raise
            except Exception as error:
                failure = ExecutionAdmissionError(error)
                self.dispatch_error = str(failure)
                raise failure from error
            return self.selected_session_id

    def _check_session_selection(self):
        # Caller holds self.lock. None is also a successfully frozen (new-session) selection.
        if (self.process is not None or self.session_selected or self.session_claim is not None
                or self.done.is_set() or self.dispatch_error):
            raise StaleRun('Session must be selected exactly once before launch')

    def select_legacy_session(self, adapter_id, legacy_key=None):
        with self.lock:
            try:
                self._check_session_selection()
                selected = self.tracker.select_legacy_session(self.token, adapter_id, legacy_key)
                self.selected_session_id = selected
                self.session_selected = True
            except Exception as error:
                failure = ExecutionAdmissionError(error)
                self.dispatch_error = str(failure)
                raise failure from error
            return self.selected_session_id

    def observe_session(self, adapter_id, output, *, normal_completion):
        """Parse every execution, including partial cancelled/error output.

        Duplicate announcements of the same ID are harmless; distinct IDs are not.
        Protocol/message outcome is deliberately not an input.
        """
        import json
        ids = set()
        backend_error = False
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(event, dict):
                continue
            if event.get('type') in {'error', 'turn.failed'}:
                backend_error = True
            key = ('thread_id' if adapter_id == 'codex' and event.get('type') == 'thread.started'
                   else 'sessionId' if adapter_id == 'dsh' and event.get('type') == 'session'
                   else 'sessionId' if adapter_id == 'grok' and event.get('type') == 'end' else None)
            if key and isinstance(event.get(key), str) and event[key].strip():
                ids.add(event[key])
        with self.lock:
            expected = self.session_claim.expected_id if self.session_claim else None
            actual = next(iter(ids)) if len(ids) == 1 else None
            status = ('invalid' if len(ids) > 1 or (actual and expected and actual != expected)
                      else 'verified' if actual else 'unverified')
            self.session_validation = SessionValidation(status, actual,
                normal_completion and not backend_error, tuple(sorted(ids)))
            return self.session_validation

    def cancel(self):
        with self.lock:
            if self.done.is_set():
                return
            self.cancelled.set()
            if self.process is not None:
                self.process.cancel()
            # A caller must still stop on persistence failure; memory/process
            # cancellation has already been accepted before this write.
            self.tracker.request_cancel(self.token)

    def launch(self, command, *, snapshot, **kwargs):
        with self.lock:
            if self.dispatch_error:
                raise RuntimeError(self.dispatch_error)
            if self.session_claim and self.session_claim.validation_only:
                # A verified restricted backend validation transport is not yet
                # available. Never turn candidate validation into a business run.
                self.dispatch_error = 'Restricted session validation transport not enabled'
                raise RuntimeError(self.dispatch_error)
            if self.process is not None:
                raise RuntimeError('A run cannot launch twice')
            self.tracker.set_launch_snapshot(self.token, snapshot)
            self.process = ManagedProcess()
            if self.cancelled.is_set():
                self.process.cancel()

        def running(_process):
            with self.lock:
                if self.cancelled.is_set():
                    raise RuntimeError('Cancelled before startup commit')
                self.tracker.mark_running(self.token)

        # Register the owner BEFORE creation, so cancellation cannot miss the
        # creation/registration gap. ManagedProcess holds its native handles.
        self.result = self.process.run(command, on_running=running,
                                       cancelled=self.cancelled.is_set, **kwargs)
        return self.result

    def finish(self, outcome):
        with self.lock:
            try:
                if self.done.is_set():
                    return self.released
                evidence = (self.result.exit_evidence if self.result else
                            'unknown' if self.process and self.process.pid else 'not_started')
                if evidence == 'not_started' and outcome == 'completed':
                    outcome = 'failed'
                if self.cancelled.is_set():
                    outcome = 'cancelled'
                self.tracker.finish_run(self.token, outcome=outcome, exit_evidence=evidence,
                    session_claim=self.session_claim, session_validation=self.session_validation)
                self.released = evidence in {'not_started', 'confirmed'}
                return self.released
            finally:
                self.done.set()
