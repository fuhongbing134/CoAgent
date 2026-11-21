"""Execution-domain data operations used by the workbench execution boundary.

The caller supplies an initialized database and instance identity. Exit evidence
is a caller assertion, NOT process verification. In particular, wait()/returncode
alone must never be translated into ``confirmed``. Real verification and startup
serialization belong to task 4B. Session finalization shares the run transaction.
"""
from contextlib import closing, contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import uuid

from workbench.storage.database_schema import require_current
from workbench.execution.execution_inspection import (InspectionQueryError, canonical_adapter,
                                  find_session_conflicts, session_owner_issue,
                                  unfinished_runs)
from workbench.execution.migration_barrier import MigrationBarrier


class DomainBusy(RuntimeError):
    """An existing owner must not be replaced."""

    def __init__(self, message, *, reason_code='domain_occupied', records=()):
        super().__init__(message)
        self.reason_code = reason_code
        self.records = tuple(records)
        self.category = ('concurrent_rejection' if self.records and all(
            r.get('category') == 'concurrent_rejection' for r in self.records)
            else 'occupancy_blocked')


class StaleRun(RuntimeError):
    """The supplied run/instance/fence no longer owns the domain."""


class SessionReviewRequired(RuntimeError):
    """A retained binding/candidate needs explicit adoption or validation."""


@dataclass(frozen=True)
class SessionIdentity:
    adapter_id: str
    backend_scope: str
    config_fingerprint: str
    workspace_id: str
    workspace_version: int = 0

    def values(self):
        values = (self.adapter_id, self.backend_scope, self.config_fingerprint,
                  self.workspace_id, self.workspace_version)
        if any(not isinstance(v, str) or not v.strip() for v in values[:4]):
            raise ValueError('Verified session identity required')
        if type(self.workspace_version) is not int or self.workspace_version < 0:
            raise ValueError('Invalid workspace version')
        return values


@dataclass(frozen=True)
class SessionClaim:
    identity: SessionIdentity
    generation: int
    expected_id: str | None
    validation_only: bool = False


@dataclass(frozen=True)
class SessionValidation:
    status: str = 'unverified'
    actual_id: str | None = None
    normal_completion: bool = False
    observed_ids: tuple = ()

    def __post_init__(self):
        if self.status not in {'verified', 'invalid', 'unverified'}:
            raise ValueError('Invalid session validation status')


def invalidate_sessions(db, conversation_id):
    """Settings transaction helper. Retain IDs/candidates; never touch legacy tables."""
    if db.execute("SELECT 1 FROM execution_domains WHERE conversation_id=? AND state!='idle'",
                  (conversation_id,)).fetchone():
        raise DomainBusy('Cannot change workspace while an execution domain is occupied')
    db.execute("""UPDATE execution_sessions SET state='needs_validation',generation=generation+1,
        owner_run_id=NULL,instance_id=NULL WHERE conversation_id=?""", (conversation_id,))


@dataclass(frozen=True)
class RunToken:
    run_id: str
    instance_id: str
    fence: int


class ExecutionTracker:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()

    @contextmanager
    def _transaction(self):
        # mode=rw deliberately refuses missing databases; initialization is explicit.
        with MigrationBarrier(self.path), closing(sqlite3.connect(
                self.path.as_uri() + '?mode=rw', uri=True, timeout=10)) as db:
            require_current(db)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            with db:
                db.execute('BEGIN IMMEDIATE')
                yield db

    def begin_run(self, *, agent_id, conversation_id, instance_id, source_kind,
                  config_snapshot, source_job_id=None, source_call_id=None,
                  source_message_id=None, workspace_id=None, workspace_version=None):
        """Create a pending run and reserve an idle domain in one transaction.

        config_snapshot must already be sanitized by the caller. This data layer
        neither resolves configuration nor reads credentials/environment variables.
        """
        for value in (agent_id, conversation_id, instance_id, source_kind):
            if not isinstance(value, str) or not value.strip():
                raise ValueError('Nonempty agent, conversation, instance and source required')
        if not isinstance(config_snapshot, dict):
            raise ValueError('config_snapshot must be a sanitized dictionary')
        snapshot = json.dumps(config_snapshot, ensure_ascii=False, sort_keys=True, allow_nan=False)
        run_id = uuid.uuid4().hex
        with self._transaction() as db:
            db.execute('INSERT OR IGNORE INTO execution_domains(agent_id,conversation_id) VALUES (?,?)',
                       (agent_id, conversation_id))
            domain = db.execute('SELECT * FROM execution_domains WHERE agent_id=? AND conversation_id=?',
                                (agent_id, conversation_id)).fetchone()
            if domain['state'] != 'idle':
                raise DomainBusy('Execution domain is occupied; no takeover permitted',
                                 reason_code=('domain_occupied_active'
                                              if domain['instance_id'] == instance_id
                                              else 'domain_occupied_retained'),
                                 records=[dict(domain)])
            unfinished = unfinished_runs(db, agent_id, conversation_id)
            if unfinished:
                raise DomainBusy('Unfinished run exists in an idle or inconsistent domain',
                                 reason_code='orphan_unfinished_run', records=unfinished)
            db.execute('''INSERT INTO execution_runs
                (run_id,agent_id,conversation_id,source_kind,source_job_id,source_call_id,
                 source_message_id,instance_id,config_snapshot,workspace_id,workspace_version,state)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending')''',
                (run_id, agent_id, conversation_id, source_kind, source_job_id, source_call_id,
                 source_message_id, instance_id, snapshot, workspace_id, workspace_version))
            changed = db.execute('''UPDATE execution_domains SET owner_run_id=?,instance_id=?,
                fence=fence+1,state='reserved' WHERE agent_id=? AND conversation_id=?
                AND state='idle' AND fence=?''',
                (run_id, instance_id, agent_id, conversation_id, domain['fence'])).rowcount
            if changed != 1:
                raise StaleRun('Domain reservation changed')
            return RunToken(run_id, instance_id, domain['fence'] + 1)

    def _owned(self, db, token):
        if not isinstance(token, RunToken) or type(token.fence) is not int or token.fence < 1:
            raise StaleRun('Invalid run token')
        run = db.execute('SELECT * FROM execution_runs WHERE run_id=? AND instance_id=?',
                         (token.run_id, token.instance_id)).fetchone()
        if run is None:
            raise StaleRun('Unknown run or instance')
        domain = db.execute('SELECT * FROM execution_domains WHERE agent_id=? AND conversation_id=?',
                            (run['agent_id'], run['conversation_id'])).fetchone()
        if (domain is None or domain['fence'] != token.fence or
                domain['owner_run_id'] != token.run_id or domain['instance_id'] != token.instance_id or
                domain['state'] not in {'reserved', 'running', 'quiescing'} or
                run['state'] not in {'pending', 'running', 'quiescing'}):
            raise StaleRun('Run no longer owns domain')
        return run, domain

    def _domain_state(self, db, token, previous, target):
        changed = db.execute('''UPDATE execution_domains SET state=?,
            owner_run_id=CASE WHEN ?='idle' THEN NULL ELSE owner_run_id END,
            instance_id=CASE WHEN ?='idle' THEN NULL ELSE instance_id END
            WHERE owner_run_id=? AND instance_id=? AND fence=? AND state=?''',
            (target, target, target, token.run_id, token.instance_id, token.fence, previous)).rowcount
        if changed != 1:
            raise StaleRun('Conditional domain update failed')

    def mark_running(self, token):
        """Record startup; caller must serialize this with cancellation/Popen."""
        with self._transaction() as db:
            run, domain = self._owned(db, token)
            if run['cancel_requested']:
                raise ValueError('Cancellation requested; startup forbidden')
            if run['state'] == 'running' and domain['state'] == 'running':
                return False
            if run['state'] != 'pending' or domain['state'] != 'reserved':
                raise ValueError('Only a reserved pending run may start')
            db.execute("UPDATE execution_runs SET state='running' WHERE run_id=?", (token.run_id,))
            self._domain_state(db, token, 'reserved', 'running')
            return True

    def set_launch_snapshot(self, token, snapshot):
        """Freeze the sanitized, actually resolved launch configuration pre-start."""
        encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, allow_nan=False)
        with self._transaction() as db:
            run, domain = self._owned(db, token)
            if run['state'] != 'pending' or domain['state'] != 'reserved':
                raise ValueError('Launch configuration is immutable after startup')
            if run['cancel_requested']:
                raise ValueError('Cancellation requested; startup forbidden')
            db.execute('UPDATE execution_runs SET config_snapshot=? WHERE run_id=?',
                       (encoded, token.run_id))

    def request_cancel(self, token):
        """Persist intent only; never claim cancellation or exit has completed."""
        with self._transaction() as db:
            run, _ = self._owned(db, token)
            if run['cancel_requested']:
                return False
            db.execute('UPDATE execution_runs SET cancel_requested=1 WHERE run_id=?', (token.run_id,))
            return True

    def claim_session(self, token, identity, *, adoption=None, tools_disabled=False):
        """Claim only after domain reservation. adoption is an explicit reviewed candidate.

        No CLI probing, timestamp selection or business-task replay occurs here.
        Review failures commit the merged evidence before reporting to the caller.
        """
        values = identity.values()
        review = None
        claim = None
        with self._transaction() as db:
            run, domain = self._owned(db, token)
            if run['cancel_requested'] or run['state'] != 'pending' or domain['state'] != 'reserved':
                raise StaleRun('Session claim requires an uncancelled reserved run')
            db.execute('INSERT OR IGNORE INTO execution_sessions(agent_id,conversation_id) VALUES (?,?)',
                       (run['agent_id'], run['conversation_id']))
            session = db.execute('SELECT * FROM execution_sessions WHERE agent_id=? AND conversation_id=?',
                                 (run['agent_id'], run['conversation_id'])).fetchone()
            if session['owner_run_id'] == token.run_id:
                raise StaleRun('Session already claimed by this run')
            issue = session_owner_issue(session, run)
            if issue:
                raise DomainBusy('Session owner is retained; no takeover permitted',
                                 reason_code=issue['reason_code'], records=[issue])
            candidates = json.loads(session['legacy_candidates_json'])
            # The v2 migration snapshot is not current: 4B kept updating these tables.
            if run['agent_id'] == 'codex':
                rows = db.execute('SELECT codex_thread_id,updated_at FROM workbench_sessions WHERE conversation_id=?',
                                  (run['conversation_id'],))
                for sid, stamp in rows:
                    candidate = dict(source_table='workbench_sessions', original_agent_key='codex',
                                     conversation_id=run['conversation_id'], session_id=sid, updated_at=stamp)
                    if candidate not in candidates:
                        candidates.append(candidate)
            rows = db.execute('SELECT agent_id,session_id,updated_at FROM workbench_agent_sessions WHERE conversation_id=? AND agent_id IN (?,?)',
                              (run['conversation_id'], run['agent_id'],
                               'deepseek-harness-headless' if run['agent_id']=='deepseek-harness'
                               else 'grok-headless' if run['agent_id']=='grok' else run['agent_id']))
            for key, sid, stamp in rows:
                candidate = dict(source_table='workbench_agent_sessions', original_agent_key=key,
                                 conversation_id=run['conversation_id'], session_id=sid, updated_at=stamp)
                if candidate not in candidates:
                    candidates.append(candidate)
            previous_identity = tuple(session[k] for k in ('adapter_id','backend_scope','config_fingerprint',
                                                          'workspace_id','workspace_version'))
            compatible = previous_identity == values
            expected = session['session_id']
            validation_only = False
            if session['state'] != 'ready' or not compatible:
                if expected or candidates:
                    # Caller must supply the exact selected evidence and reviewed identity.
                    # An ID echo alone cannot establish ownership of a legacy session.
                    if (not adoption or adoption.get('candidate') not in candidates or
                            adoption.get('identity') != identity or
                            adoption.get('confirmed') is not True or not tools_disabled):
                        review = 'Session needs explicit candidate adoption and tool-disabled validation'
                    else:
                        expected = adoption['candidate'].get('session_id')
                        if not isinstance(expected, str) or not expected.strip():
                            review = 'Selected candidate has no valid session ID'
                        validation_only = True
                        candidate = adoption['candidate']
                        if (candidate.get('conversation_id', run['conversation_id']) != run['conversation_id'] or
                                any(candidate.get(k) is not None and candidate[k] != v
                                    for k, v in zip(('adapter_id','backend_scope','config_fingerprint',
                                                     'workspace_id','workspace_version'), values))):
                            review = 'Candidate provenance/namespace/workspace is incompatible'
                else:
                    expected = None
            # Only a usable selection is admitted. Existing review failures still
            # persist candidate evidence below, without claiming/launching a CLI.
            if not review:
                self._check_session_available(db, run, identity.adapter_id, expected,
                                              backend_scope=identity.backend_scope)
            # Keep the formerly bound ID as evidence when identity changes.
            if session['session_id'] and (not compatible or session['state'] != 'ready'):
                bound = {k: session[k] for k in ('adapter_id','backend_scope','config_fingerprint',
                                                'workspace_id','workspace_version','session_id')}
                bound['source_table'] = 'execution_sessions'
                bound['conversation_id'] = run['conversation_id']
                if bound not in candidates:
                    candidates.append(bound)
            encoded = json.dumps(candidates, ensure_ascii=False, sort_keys=True)
            if review:
                db.execute("""UPDATE execution_sessions SET state='needs_validation',
                    legacy_candidates_json=?,generation=generation+? WHERE agent_id=? AND conversation_id=?""",
                    (encoded, int(session['state'] == 'ready'), run['agent_id'], run['conversation_id']))
            else:
                generation = session['generation'] + 1
                changed = db.execute("""UPDATE execution_sessions SET adapter_id=?,backend_scope=?,config_fingerprint=?,
                    workspace_id=?,workspace_version=?,session_id=?,generation=?,owner_run_id=?,instance_id=?,
                    state='needs_validation',legacy_candidates_json=? WHERE agent_id=? AND conversation_id=?
                    AND generation=?""", (*values, expected, generation, token.run_id, token.instance_id,
                    encoded, run['agent_id'], run['conversation_id'], session['generation'])).rowcount
                if changed != 1:
                    raise StaleRun('Session claim generation changed')
                db.execute('UPDATE execution_runs SET workspace_id=?,workspace_version=? WHERE run_id=?',
                           (identity.workspace_id, identity.workspace_version, token.run_id))
                claim = SessionClaim(identity, generation, expected, validation_only)
        if review:
            raise SessionReviewRequired(review)
        return claim

    @staticmethod
    def _check_session_available(db, run, adapter_id, session_id, *, backend_scope=None):
        conflicts = find_session_conflicts(db, agent_id=run['agent_id'],
            conversation_id=run['conversation_id'], adapter_id=adapter_id,
            session_id=session_id, backend_scope=backend_scope,
            current_run_id=run['run_id'], current_instance_id=run['instance_id'])
        if conflicts:
            raise DomainBusy('Selected session has an associated execution owner',
                             reason_code='shared_session_occupied', records=conflicts)

    def select_legacy_session(self, token, adapter_id, legacy_key=None):
        """Select a fixed legacy ID under the admission write transaction.

        No unified claim is created. Visible binding/domain associations cover
        fixed legacy IDs only; they do not establish historical writer identity.
        """
        adapter_id = canonical_adapter(adapter_id)
        if adapter_id not in {'codex', 'dsh', 'grok'}:
            raise ValueError('Unsupported legacy session adapter')
        if adapter_id == 'dsh':
            legacy_key = legacy_key or 'deepseek-harness-headless'
            if legacy_key not in {'deepseek-harness', 'deepseek-harness-headless', 'dsh'}:
                raise ValueError('Invalid DSH legacy session key')
        elif adapter_id == 'grok':
            legacy_key = legacy_key or 'grok-headless'
            if legacy_key not in {'grok', 'grok-headless'}:
                raise ValueError('Invalid Grok legacy session key')
        elif legacy_key not in {None, 'codex'}:
            raise ValueError('Invalid Codex legacy session key')
        with self._transaction() as db:
            run, domain = self._owned(db, token)
            if (run['cancel_requested'] or run['state'] != 'pending'
                    or domain['state'] != 'reserved'):
                raise StaleRun('Session selection requires an uncancelled reserved run')
            if canonical_adapter(run['agent_id']) != adapter_id:
                raise ValueError('Legacy adapter does not match execution agent')
            session = db.execute('SELECT * FROM execution_sessions WHERE agent_id=? AND conversation_id=?',
                                 (run['agent_id'], run['conversation_id'])).fetchone()
            issue = session_owner_issue(session, run)
            if issue:
                raise DomainBusy('Session owner is retained; no takeover permitted',
                                 reason_code=issue['reason_code'], records=[issue])
            if session is not None and session['owner_run_id'] == token.run_id:
                raise StaleRun('Unified session already claimed; legacy selection forbidden')
            if adapter_id == 'codex':
                row = db.execute('SELECT codex_thread_id FROM workbench_sessions WHERE conversation_id=?',
                                 (run['conversation_id'],)).fetchone()
            else:
                row = db.execute('SELECT session_id FROM workbench_agent_sessions WHERE conversation_id=? AND agent_id=?',
                                 (run['conversation_id'], legacy_key)).fetchone()
            selected = row[0] if row is not None else None
            if row is not None and (not isinstance(selected, str) or not selected.strip()):
                raise InspectionQueryError('Invalid legacy session binding')
            self._check_session_available(db, run, adapter_id, selected)
            return selected

    def _finish_session(self, db, token, run, claim, validation, evidence, outcome):
        session = db.execute('SELECT * FROM execution_sessions WHERE agent_id=? AND conversation_id=?',
                             (run['agent_id'], run['conversation_id'])).fetchone()
        keys = ('adapter_id','backend_scope','config_fingerprint','workspace_id','workspace_version')
        if (session is None or session['generation'] != claim.generation or
                session['owner_run_id'] != token.run_id or session['instance_id'] != token.instance_id or
                tuple(session[k] for k in keys) != claim.identity.values() or
                session['session_id'] != claim.expected_id):
            raise StaleRun('Session ownership/generation/identity changed; finalization rolled back')
        if (run['workspace_id'], run['workspace_version']) != (claim.identity.workspace_id, claim.identity.workspace_version):
            raise StaleRun('Run workspace identity changed')
        actual = validation.actual_id
        if actual is not None and (not isinstance(actual, str) or not actual.strip()):
            raise ValueError('Invalid actual session ID')
        matched = bool(actual) and (claim.expected_id is None or actual == claim.expected_id)
        ready = (validation.status == 'verified' and matched and validation.normal_completion and
                 evidence == 'confirmed' and not run['cancel_requested'] and outcome != 'cancelled')
        candidates = json.loads(session['legacy_candidates_json'])
        if not ready:
            observed = set(validation.observed_ids)
            if actual:
                observed.add(actual)
            for sid in sorted(observed):
                if not isinstance(sid, str) or not sid.strip():
                    raise ValueError('Invalid observed session ID')
                candidate = dict(source_table='execution_result', session_id=sid,
                                 run_id=token.run_id, backend_scope=claim.identity.backend_scope,
                                 adapter_id=claim.identity.adapter_id, workspace_id=claim.identity.workspace_id,
                                 workspace_version=claim.identity.workspace_version,
                                 config_fingerprint=claim.identity.config_fingerprint,
                                 conversation_id=run['conversation_id'])
                if candidate not in candidates:
                    candidates.append(candidate)
        # A mismatch must never replace the expected binding. Unknown exits retain claim ownership.
        sid = actual if ready else session['session_id']
        db.execute("""UPDATE execution_sessions SET session_id=?,state=?,legacy_candidates_json=?,
            owner_run_id=?,instance_id=? WHERE agent_id=? AND conversation_id=? AND generation=?
            AND owner_run_id=? AND instance_id=?""", (sid, 'ready' if ready else 'needs_validation',
            json.dumps(candidates, ensure_ascii=False, sort_keys=True),
            token.run_id if evidence == 'unknown' else None,
            token.instance_id if evidence == 'unknown' else None,
            run['agent_id'], run['conversation_id'], claim.generation, token.run_id, token.instance_id))

    def finish_run(self, token, *, outcome, exit_evidence='unknown', session_claim=None,
                   session_validation=None):
        """Finish or retain ownership. Evidence: not_started / confirmed / unknown.

        not_started asserts no process was ever created (only pending/reserved).
        confirmed asserts the ENTIRE execution has exited, verified externally.
        unknown always retains ownership, even when the direct child has exited.
        Terminal duplicates are no-ops only while their fence is still current.
        """
        if outcome not in {'completed', 'failed', 'cancelled'}:
            raise ValueError('Invalid outcome')
        if exit_evidence not in {'not_started', 'confirmed', 'unknown'}:
            raise ValueError('Invalid exit evidence')
        with self._transaction() as db:
            # Check identity/fence even for a duplicate; never affect a newer owner.
            if isinstance(token, RunToken) and type(token.fence) is int:
                previous = db.execute('''SELECT r.state,r.cancel_requested,d.fence,d.state AS domain_state
                    FROM execution_runs r JOIN execution_domains d
                    ON r.agent_id=d.agent_id AND r.conversation_id=d.conversation_id
                    WHERE r.run_id=? AND r.instance_id=?''', (token.run_id, token.instance_id)).fetchone()
                if (previous and previous['state'] in {'completed', 'failed', 'cancelled'}
                        and previous['fence'] == token.fence and previous['domain_state'] == 'idle'):
                    effective = 'cancelled' if previous['cancel_requested'] else outcome
                    if previous['state'] != effective:
                        raise ValueError('Terminal outcome cannot be overwritten')
                    return False
            run, domain = self._owned(db, token)
            if exit_evidence == 'not_started' and (
                    run['state'] != 'pending' or domain['state'] != 'reserved' or outcome == 'completed'):
                raise ValueError('not_started requires a pending failed/cancelled run')
            if session_claim is not None:
                self._finish_session(db, token, run, session_claim,
                                     session_validation or SessionValidation(), exit_evidence, outcome)
            state = ('quiescing' if exit_evidence == 'unknown' else
                     'cancelled' if run['cancel_requested'] else outcome)
            db.execute('UPDATE execution_runs SET state=?,exit_confirmed=? WHERE run_id=?',
                       (state, int(exit_evidence == 'confirmed'), token.run_id))
            self._domain_state(db, token, domain['state'], 'quiescing' if state == 'quiescing' else 'idle')
            return True
