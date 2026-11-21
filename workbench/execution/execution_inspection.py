"""Shared execution observations; caller owns the connection and transaction.

Shared query helpers only SELECT on the caller's connection. The explicit
read_occupancy_snapshot entry point owns a read-only connection and barrier.
Current bindings never prove historical writer identity or process liveness.
No application initialization, repair, or process queries are performed.
"""
from datetime import datetime, timezone
import json


UNFINISHED_STATES = ('pending', 'running', 'quiescing')
RUN_COLUMNS = ('run_id,agent_id,conversation_id,source_kind,source_job_id,'
               'source_call_id,source_message_id,instance_id,state,'
               'cancel_requested,exit_confirmed')


class InspectionQueryError(RuntimeError):
    reason_code = 'query_failed'
    category = 'query_failed'


def _rows(db, sql, parameters=()):
    cursor = db.execute(sql, parameters)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def canonical_adapter(agent_id):
    return {'codex': 'codex', 'dsh': 'dsh',
            'deepseek-harness': 'dsh',
            'deepseek-harness-headless': 'dsh',
            'grok': 'grok', 'grok-headless': 'grok'}.get(agent_id, agent_id)


def _key(row):
    return row['agent_id'], row['conversation_id']


def _issue(reason_code, row, *, category='occupancy_blocked'):
    return dict(reason_code=reason_code, category=category,
                agent_id=row['agent_id'], conversation_id=row['conversation_id'],
                run_id=row.get('run_id') or row.get('owner_run_id'),
                instance_id=row.get('instance_id'))


def unfinished_runs(db, agent_id, conversation_id):
    return _rows(db, 'SELECT ' + RUN_COLUMNS + ''' FROM execution_runs
        WHERE agent_id=? AND conversation_id=?
        AND state IN ('pending','running','quiescing') ORDER BY run_id''',
        (agent_id, conversation_id))


def read_execution_records(db):
    """Read within a caller-provided consistent snapshot; do not expose config."""
    domains = _rows(db, 'SELECT * FROM execution_domains ORDER BY agent_id,conversation_id')
    runs = _rows(db, 'SELECT ' + RUN_COLUMNS + ' FROM execution_runs ORDER BY run_id')
    sessions = _rows(db, 'SELECT * FROM execution_sessions ORDER BY agent_id,conversation_id')
    for session in sessions:
        try:
            candidates = json.loads(session.pop('legacy_candidates_json'))
            if not isinstance(candidates, list) or any(not isinstance(c, dict) for c in candidates):
                raise ValueError('Invalid candidate list')
        except (ValueError, TypeError) as error:
            raise InspectionQueryError('Invalid session candidate metadata') from error
        # Raw candidate payloads may contain unreviewed data. Counts suffice for inspection.
        session['candidate_count'] = len(candidates)
    bindings = _rows(db, '''SELECT 'codex' AS agent_id,conversation_id,
        codex_thread_id AS session_id,'workbench_sessions' AS source_table
        FROM workbench_sessions ORDER BY conversation_id''')
    bindings += _rows(db, '''SELECT agent_id,conversation_id,session_id,
        'workbench_agent_sessions' AS source_table FROM workbench_agent_sessions
        ORDER BY agent_id,conversation_id''')
    for binding in bindings:
        binding['adapter_id'] = canonical_adapter(binding['agent_id'])
        if binding['adapter_id'] == 'dsh':
            binding['agent_id'] = 'deepseek-harness'
        elif binding['adapter_id'] == 'grok':
            binding['agent_id'] = 'grok'
        binding['backend_scope'] = None
        binding['scope_relation'] = 'scope_unknown'
    return dict(domains=domains, runs=runs, sessions=sessions, bindings=bindings)


def session_owner_issue(session, run):
    """Only a matching current owner may be treated as already legitimately held."""
    if session is None or session['owner_run_id'] is None:
        return None
    session, run = dict(session), dict(run)
    if (session['owner_run_id'] != run['run_id'] or _key(session) != _key(run)
            or session['instance_id'] != run['instance_id']):
        return _issue('session_owner_retained', session)
    return None


def find_session_conflicts(db, *, agent_id, conversation_id, adapter_id,
                           session_id, backend_scope=None, current_run_id=None,
                           current_instance_id=None):
    """Find visible occupied bindings in the supplied admission transaction.

    Config/workspace fingerprints do not split one backend session namespace.
    A missing scope alone is not a conflict. Matching legacy IDs require the
    same adapter, and are explicitly only suspected namespace matches.
    """
    if session_id is None:
        return []
    if not isinstance(session_id, str) or not session_id.strip():
        raise InspectionQueryError('Invalid selected session ID')
    records = read_execution_records(db)
    adapter_id = canonical_adapter(adapter_id)
    domains = {_key(d): d for d in records['domains']}
    runs = records['runs']
    runs_by_id = {r['run_id']: r for r in runs}
    sessions = {_key(s): s for s in records['sessions']}
    conflicts = []
    seen = set()

    def add(row, reason, scope_relation):
        # A wrong instance/key with the same run ID must not be excluded.
        if (row.get('run_id', row.get('owner_run_id')) == current_run_id
                and row.get('instance_id') == current_instance_id
                and _key(row) == (agent_id, conversation_id)):
            return
        identity = (_key(row), row.get('run_id', row.get('owner_run_id')), reason)
        if identity in seen:
            return
        seen.add(identity)
        domain = domains.get(_key(row))
        same_startup = (current_instance_id is not None
                        and row.get('instance_id') == current_instance_id)
        # Classify as concurrent only a consistent current reserved pending run.
        owner_run = runs_by_id.get(row.get('run_id', row.get('owner_run_id')))
        concurrent = (same_startup and owner_run and owner_run['state'] == 'pending'
                      and _key(owner_run) == _key(row)
                      and owner_run['instance_id'] == current_instance_id and domain
                      and domain['state'] == 'reserved'
                      and domain['owner_run_id'] == owner_run['run_id']
                      and domain['instance_id'] == current_instance_id)
        issue = _issue(reason, row, category='concurrent_rejection' if concurrent
                       else 'occupancy_blocked')
        issue['scope_relation'] = scope_relation
        conflicts.append(issue)

    for binding in records['bindings'] + records['sessions']:
        if (binding['session_id'] != session_id
                or canonical_adapter(binding.get('adapter_id') or binding['agent_id']) != adapter_id):
            continue
        scope = binding.get('backend_scope')
        if backend_scope is not None and scope is not None and scope != backend_scope:
            continue
        relation = 'same_scope' if backend_scope is not None and scope == backend_scope else 'scope_unknown'
        key = _key(binding)
        domain = domains.get(key)
        if domain and domain['state'] != 'idle':
            add(domain, 'shared_domain_occupied', relation)
        for run in runs:
            if _key(run) == key and run['state'] in UNFINISHED_STATES:
                add(run, 'shared_run_unfinished', relation)
        session = sessions.get(key)
        if session is not None and session['owner_run_id'] is not None:
            add(session, 'shared_session_owner_retained', relation)
    return conflicts


def classify_occupancy(db, *, current_instance_id=None):
    """Union of occupied domains, unfinished runs and retained session owners.

    Owner relation describes record provenance only, never process liveness.
    Idle historical bindings/candidates remain visible without becoming locks.
    """
    records = read_execution_records(db)
    domains = {_key(d): d for d in records['domains']}
    runs = {r['run_id']: r for r in records['runs']}
    keys = set(domains) | {_key(r) for r in records['runs'] if r['state'] in UNFINISHED_STATES}
    keys |= {_key(s) for s in records['sessions']} | {_key(b) for b in records['bindings']}
    observed_at = datetime.now(timezone.utc).isoformat()
    results = []
    for key in sorted(keys):
        domain = domains.get(key)
        active = [r for r in records['runs'] if _key(r) == key and r['state'] in UNFINISHED_STATES]
        session = next((s for s in records['sessions'] if _key(s) == key), None)
        bindings = [b for b in records['bindings'] if _key(b) == key]
        issues = []
        owner = runs.get(domain['owner_run_id']) if domain else None
        occupied = domain is not None and domain['state'] != 'idle'
        if occupied and (owner is None or _key(owner) != key
                         or owner['instance_id'] != domain['instance_id']
                         or owner['state'] not in UNFINISHED_STATES):
            issues.append(_issue('domain_owner_inconsistent', domain))
        for run in active:
            if (not occupied or domain['owner_run_id'] != run['run_id']
                    or domain['instance_id'] != run['instance_id']):
                issues.append(_issue('orphan_unfinished_run', run))
        if session is not None and session['owner_run_id'] is not None:
            session_run = runs.get(session['owner_run_id'])
            if (session_run is None or _key(session_run) != key
                    or session_run['instance_id'] != session['instance_id']
                    or session_run['state'] not in UNFINISHED_STATES
                    or not occupied or domain['owner_run_id'] != session['owner_run_id']
                    or domain['instance_id'] != session['instance_id']):
                issues.append(_issue('session_owner_retained', session))
        instance = domain['instance_id'] if occupied else None
        relation = ('unknown' if instance is None or current_instance_id is None else
                    'current' if instance == current_instance_id else 'previous')
        if issues:
            classification = 'record_inconsistent'
        elif occupied:
            classification = {'current': 'current_instance_occupied',
                              'previous': 'previous_instance_retained',
                              'unknown': 'instance_relation_unknown'}[relation]
        elif session and session['state'] == 'needs_validation':
            classification = 'session_needs_validation'
        else:
            classification = 'no_recorded_occupancy'
        related_ids = {r['run_id'] for r in active}
        if domain and domain['owner_run_id'] is not None:
            related_ids.add(domain['owner_run_id'])
        if session and session['owner_run_id'] is not None:
            related_ids.add(session['owner_run_id'])
        results.append(dict(agent_id=key[0], conversation_id=key[1],
                            classification=classification,
                            reason_code=issues[0]['reason_code'] if issues else classification,
                            owner_relation=relation, observed_at=observed_at,
                            exit_status='unknown', stage_updated_at=None,
                            blocked=bool(occupied or active or issues), domain=domain,
                            runs=[runs[rid] for rid in sorted(related_ids) if rid in runs],
                            session=session, legacy_bindings=bindings,
                            issues=issues))
    return results


def read_occupancy_snapshot(path, *, current_instance_id=None, agent_id=None,
                            conversation_id=None):
    """Read one WAL-aware snapshot; never initialize, migrate or repair a DB.

    Necessary migration-lock/SQLite coordination files are allowed. A failure
    raises a safe error, rather than returning an empty/safe-looking snapshot.
    """
    from contextlib import closing
    from pathlib import Path
    import os
    import sqlite3
    from workbench.storage.database_schema import require_current
    from workbench.execution.migration_barrier import MigrationBarrier

    try:
        database = Path(os.path.normcase(str(Path(path).expanduser().resolve())))
        if not database.is_file():
            raise InspectionQueryError('Database is missing or is not a regular file')
        with MigrationBarrier(database), closing(sqlite3.connect(
                database.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            require_current(db)
            entries = classify_occupancy(db, current_instance_id=current_instance_id)
            # Filter after the complete consistency scan; corrupt records must
            # not silently turn into a successful "no occupancy" result.
            return [entry for entry in entries
                    if (agent_id is None or entry['agent_id'] == agent_id)
                    and (conversation_id is None or entry['conversation_id'] == conversation_id)]
    except InspectionQueryError:
        raise
    except Exception as error:
        raise InspectionQueryError(
            'Occupancy snapshot unavailable; check database version/access and retry after migration') from error
