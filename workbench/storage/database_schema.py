"""Explicit atomic schema migration; legacy writers must be stopped at first upgrade."""
import sqlite3
import uuid
import re
import json
import logging
from collections import Counter
from contextlib import closing
from pathlib import Path
from workbench.execution.migration_barrier import MigrationBarrier
from workbench.execution.service_instance import ServiceInstanceLock
from workbench.storage.schema_definition import SCHEMA_SQL, EXECUTION_SCHEMA_SQL

SCHEMA_VERSION = 2


def _check_tables(db, version=SCHEMA_VERSION):
    expected = set(re.findall(r'CREATE TABLE IF NOT EXISTS (\w+)', SCHEMA_SQL)) | {'legacy_session_imports'}
    if version >= 2:
        expected |= set(re.findall(r'CREATE TABLE IF NOT EXISTS (\w+)', EXECUTION_SCHEMA_SQL))
    actual = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not expected <= actual:
        raise RuntimeError('Schema version marker inconsistent with tables: ' + ', '.join(sorted(expected-actual)))


def require_current(db):
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version != SCHEMA_VERSION:
        raise RuntimeError(f'Schema version {version} is unsupported here; expected {SCHEMA_VERSION}. Run coordinated initialization.')


def _statements(script):
    statement = ''
    for char in script:
        statement += char
        if char == ';' and sqlite3.complete_statement(statement):
            yield statement
            statement = ''
    if statement.strip():
        raise ValueError('Incomplete schema statement')


def _legacy_sessions(db):
    """Stage every source row; agent_key is an opaque import identity, never a resume key.

    Ordinary keys remain unchanged. Duplicate domains/session bindings use a reversible
    source-qualified JSON identity, preserving the v1 schema and both original sources.
    The prefix is chosen outside ALL input key namespaces to prevent collisions with
    arbitrary historical keys. Task 3 must resolve these candidates before binding.
    """
    db.execute('''CREATE TABLE legacy_session_imports (
        agent_key TEXT NOT NULL, conversation_id TEXT NOT NULL, session_id TEXT NOT NULL,
        updated_at TEXT NOT NULL, source_table TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'needs_validation',
        PRIMARY KEY(agent_key,conversation_id), UNIQUE(agent_key,session_id))''')
    rows = [('codex',room,sid,stamp,'workbench_sessions') for room,sid,stamp in
            db.execute('SELECT conversation_id,codex_thread_id,updated_at FROM workbench_sessions').fetchall()]
    rows += [(key,room,sid,stamp,'workbench_agent_sessions') for room,key,sid,stamp in
             db.execute('SELECT conversation_id,agent_id,session_id,updated_at FROM workbench_agent_sessions').fetchall()]
    rows.sort()
    domains = Counter((key,room) for key,room,sid,stamp,source in rows)
    bindings = Counter((key,sid) for key,room,sid,stamp,source in rows)
    raw_keys = {row[0] for row in rows}
    prefix = 'legacy-source:'
    while any(key.startswith(prefix) for key in raw_keys):
        prefix = '_' + prefix
    report = []
    for key,room,sid,stamp,source in rows:
        reasons = []
        if key.startswith('discussion:'):
            reasons.append('isolated_discussion_key')
        elif key not in ('codex','deepseek-harness-headless','grok','grok-headless'):
            reasons.append('unrecognized_legacy_key')
        if not sid:
            reasons.append('empty_session_id')
        duplicate = domains[key,room] > 1 or bindings[key,sid] > 1
        if duplicate:
            reasons.append('conflicting_or_duplicate_binding')
            import_key = prefix + json.dumps([source,key,room], ensure_ascii=False, separators=(',',':'))
        else:
            import_key = key
        db.execute('INSERT INTO legacy_session_imports(agent_key,conversation_id,session_id,updated_at,source_table,state) VALUES (?,?,?,?,?,?)',
                   (import_key,room,sid,stamp,source,'needs_validation'))
        report.append(dict(agent_key=key, import_key=import_key, conversation_id=room,
                           source_table=source, state='needs_validation',
                           reasons=reasons or ['backend_identity_unverified']))
    return report


def _enable_wal(db):
    # SQLite cannot change journal mode inside the migration transaction.
    mode = db.execute('PRAGMA journal_mode=WAL').fetchone()[0]
    if mode.lower() != 'wal':
        raise RuntimeError('SQLite did not accept WAL mode: ' + mode)


def _execution_candidates(db):
    groups = {}
    columns = ['agent_key','conversation_id','session_id','updated_at','source_table','state']
    for values in db.execute('SELECT '+','.join(columns)+' FROM legacy_session_imports ORDER BY agent_key,conversation_id'):
        candidate = dict(zip(columns,values))
        key,room,sid,stamp,source,_ = values
        original = key
        if source == 'workbench_sessions':
            original = 'codex'
        elif source == 'workbench_agent_sessions':
            source_keys = {r[0] for r in db.execute('SELECT agent_id FROM workbench_agent_sessions WHERE conversation_id=? AND session_id=? AND updated_at=?',(room,sid,stamp))}
            if key not in source_keys and 'legacy-source:' in key:
                try:
                    decoded = json.loads(key.split('legacy-source:',1)[1])
                except (ValueError,TypeError):
                    decoded = None
                if isinstance(decoded,list) and len(decoded)==3 and decoded[0]==source and decoded[2]==room and decoded[1] in source_keys:
                    original = decoded[1]
        candidate['original_agent_key'] = original
        agent = {'codex':'codex','deepseek-harness':'deepseek-harness','deepseek-harness-headless':'deepseek-harness','grok':'grok','grok-headless':'grok'}.get(original,original)
        groups.setdefault((agent,room),[]).append(candidate)
    for (agent,room),candidates in sorted(groups.items()):
        # No guessed backend scope, workspace, generation or resumable ID.
        # Conflicts remain separate candidates in the one real execution domain.
        db.execute('INSERT INTO execution_sessions(agent_id,conversation_id,state,legacy_candidates_json) VALUES (?,?,?,?)',
                   (agent,room,'needs_validation',json.dumps(candidates,ensure_ascii=False,sort_keys=True)))
        db.execute('INSERT INTO execution_domains(agent_id,conversation_id) VALUES (?,?)',(agent,room))


def ensure_schema(path, *, allow_legacy=False, fail_at=None):
    path = ServiceInstanceLock(path).database_path
    with MigrationBarrier(path):
        if path.exists():
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
                version = db.execute('PRAGMA user_version').fetchone()[0]
                if version == SCHEMA_VERSION:
                    _check_tables(db)
                    return None
                if version > SCHEMA_VERSION:
                    raise RuntimeError('Unsupported future schema version: '+str(version))
    with MigrationBarrier(path, exclusive=True):
        with closing(sqlite3.connect(path, isolation_level=None, timeout=10)) as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version == SCHEMA_VERSION:
                _check_tables(db)
                return None
            if version > SCHEMA_VERSION:
                raise RuntimeError('Unsupported future schema version: '+str(version))
            if version not in (0,1):
                raise RuntimeError('Unsupported schema version: '+str(version))
            if version == 1:
                _check_tables(db, version=1)
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if tables and not allow_legacy:
                raise RuntimeError('Legacy upgrade requires stopping all old writers and --allow-legacy-upgrade acknowledgement')
            backup = None
            if tables:
                backup = path.with_name(path.name + '.pre-v'+str(SCHEMA_VERSION)+'-' + uuid.uuid4().hex + '.backup')
                with closing(sqlite3.connect(backup)) as target:
                    db.backup(target)
            try:
                _enable_wal(db)
            except Exception as error:
                raise RuntimeError('WAL configuration failed before schema migration; schema/data/version not migrated') from error
            db.execute('BEGIN IMMEDIATE')
            try:
                review_items = []
                if version == 0:
                    for statement in _statements(SCHEMA_SQL):
                        db.execute(statement)
                    # Upgrade the actual older workbench status constraint, preserving rows.
                    sql = db.execute("SELECT sql FROM sqlite_master WHERE name='workbench_messages'").fetchone()[0]
                    if "'cancelled'" not in sql:
                        db.execute('DROP INDEX IF EXISTS idx_workbench_messages_conversation')
                        db.execute('ALTER TABLE workbench_messages RENAME TO workbench_messages_before_cancel')
                        for statement in _statements(SCHEMA_SQL):
                            if 'CREATE TABLE IF NOT EXISTS workbench_messages' in statement:
                                db.execute(statement)
                        db.execute('INSERT INTO workbench_messages SELECT * FROM workbench_messages_before_cancel')
                        db.execute('DROP TABLE workbench_messages_before_cancel')
                        db.execute('CREATE INDEX idx_workbench_messages_conversation ON workbench_messages(conversation_id,id)')
                    review_items = _legacy_sessions(db)
                for statement in _statements(EXECUTION_SCHEMA_SQL):
                    db.execute(statement)
                if fail_at == 'ddl': raise RuntimeError('injected ddl failure')
                _execution_candidates(db)
                if fail_at == 'data': raise RuntimeError('injected data failure')
                db.execute('PRAGMA user_version=2')
                if fail_at == 'version': raise RuntimeError('injected version failure')
                db.commit()
            except BaseException:
                db.rollback()
                raise
            if review_items:
                logging.warning('Legacy session review required (not resume authorization): %s',
                                json.dumps(review_items, ensure_ascii=False, sort_keys=True))
            return backup


def restore_backup(path, backup, *, writers_stopped=False):
    if not writers_stopped:
        raise RuntimeError('Restore requires all writers stopped')
    identity = ServiceInstanceLock(path)
    with identity, MigrationBarrier(identity.database_path, exclusive=True):
        with closing(sqlite3.connect(Path(backup).resolve().as_uri()+'?mode=ro',uri=True)) as source, closing(sqlite3.connect(identity.database_path)) as target:
            if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Invalid backup')
            source.backup(target)
