"""6A acceptance: disposable v2 databases and fake transports, never real Agents.

Run with python -B verify_task6a.py. Each numbered test maps to design section 7.
Native SQLite transactions, WAL reads and cross-process migration locks are real.
"""
from contextlib import closing, ExitStack, redirect_stdout, redirect_stderr
from io import StringIO
import json
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from workbench.storage.database_schema import ensure_schema
from workbench.execution.execution_inspection import (InspectionQueryError, classify_occupancy,
                                  find_session_conflicts, read_occupancy_snapshot)
from workbench.execution.execution_runtime import Execution, ExecutionAdmissionError, format_execution_error
from workbench.execution.execution_tracking import (DomainBusy, ExecutionTracker, SessionIdentity,
                                SessionReviewRequired, SessionValidation)
from workbench.execution.managed_process import ProcessResult
from workbench.collaboration.collaboration_service import CollaborationService
from workbench.collaboration.collaboration_protocol import HANDOFF_FIELDS
import inspect_execution as inspect_cli
import web_chat as web


ROOT = Path(__file__).resolve().parents[2]


def envelope():
    value = dict(protocol_version=1, status='completed', questions=[],
                 handoff={key: 'synthetic acceptance fixture' for key in HANDOFF_FIELDS})
    return 'Synthetic answer\n```agent-collaboration\n' + json.dumps(value) + '\n```'


class Acceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='task6a-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'acceptance.db'
        ensure_schema(self.path)
        self.tracker = ExecutionTracker(self.path)
        self.launched = []
        self.executions = []
        self.serial = 0
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.addCleanup(self.clear_registry)
        self.patch_file = self.root / 'fake.yml'
        self.patch_file.write_text('{}', encoding='utf-8')
        for name, value in [('DB_PATH', self.path), ('SERVICE_INSTANCE_ID', 'current'),
                            ('DSH_AUTH_PATCH', self.patch_file), ('COLLAB_SERVICE', None),
                            ('SESSION_CONFIGURATION_RESOLVER', None)]:
            self.stack.enter_context(patch.object(web, name, value))
        self.stack.enter_context(patch.object(web, 'resolve_working_directory', return_value=self.root))
        self.stack.enter_context(patch.object(web, 'resolve_codex_command', return_value=sys.executable))
        self.stack.enter_context(patch.object(web, 'resolve_dsh_command', return_value=sys.executable))
        self.stack.enter_context(patch.object(web, 'resolve_dsh_desktop_runtime', return_value=None))
        self.stack.enter_context(patch.object(web, 'build_agent_prompt', side_effect=lambda *a: 'fixture'))
        self.stack.enter_context(patch('workbench.execution.execution_runtime.ManagedProcess.run',
                                      lambda owner, command, **kw: self.fake_run(owner, command, **kw)))
        original_begin = web.begin_execution

        def begin(*args, **kwargs):
            execution = original_begin(*args, **kwargs)
            self.executions.append(execution)
            return execution
        self.stack.enter_context(patch.object(web, 'begin_execution', side_effect=begin))

    def clear_registry(self):
        with web.ACTIVE_RUNS_LOCK:
            web.ACTIVE_RUNS.clear()
            web.COLLAB_MESSAGES.clear()

    def sql(self, statement, args=()):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.row_factory = sqlite3.Row
            cur = db.execute(statement, args)
            return [dict(r) for r in cur] if cur.description else []

    def dump(self):
        with closing(sqlite3.connect(self.path)) as db:
            return tuple(db.iterdump()), db.execute('PRAGMA user_version').fetchone()[0]

    def begin(self, room, instance='old', agent='codex', message=None):
        return self.tracker.begin_run(agent_id=agent, conversation_id=room, instance_id=instance,
            source_kind='workbench', source_message_id=message, config_snapshot={})

    def identity(self, agent='codex', scope=None):
        adapter = 'codex' if agent == 'codex' else 'dsh'
        return SessionIdentity(adapter, scope or 'synthetic:' + adapter, 'config', str(self.root), 0)

    def ready(self, room, sid, agent='codex', scope=None):
        token = self.begin(room, agent=agent)
        identity = self.identity(agent, scope)
        claim = self.tracker.claim_session(token, identity)
        self.tracker.mark_running(token)
        self.tracker.finish_run(token, outcome='completed', exit_evidence='confirmed',
            session_claim=claim, session_validation=SessionValidation('verified', sid, True))
        return token

    def legacy(self, room, sid, agent='codex'):
        if agent == 'codex':
            self.sql('INSERT OR REPLACE INTO workbench_sessions VALUES (?,?,?)', (room, sid, 'fixture'))
        else:
            self.sql('INSERT OR REPLACE INTO workbench_agent_sessions VALUES (?,?,?,?)',
                     (room, web.DSH_SESSION_KEY, sid, 'fixture'))

    def resolver(self, agent, *args):
        return SimpleNamespace(identity=self.identity(agent), evidence_reference='synthetic-only',
                               check_launch=lambda *a: None)

    def fake_run(self, owner, command, **kwargs):
        # Replaces only the OS transport; real runtime and tracker are exercised.
        kwargs['on_running'](owner)
        self.launched.append(list(command))
        sid = ('fixture-' + str(len(self.launched)))
        if '--output-last-message' in command:
            if 'resume' in command:
                sid = command[-2]
            Path(command[command.index('--output-last-message') + 1]).write_text(envelope(), encoding='utf-8')
            output = json.dumps(dict(type='thread.started', thread_id=sid)) + '\n'
        else:
            if '--session-id' in command:
                sid = command[command.index('--session-id') + 1]
            output = json.dumps(dict(type='session', sessionId=sid)) + '\n'
            output += json.dumps(dict(type='final', text=envelope())) + '\n'
        return ProcessResult('completed', 0, output.encode(), b'', 'confirmed')

    def invoke(self, kind, enabled, room, agent='codex'):
        self.serial += 1
        with patch.object(web, 'SESSION_CONFIGURATION_RESOLVER', self.resolver if enabled else None):
            if kind == 'ordinary':
                number = self.sql("INSERT INTO workbench_messages(conversation_id,sender_id,recipient_id,content,created_at,status) VALUES (?,?,'user','','','pending') RETURNING id", (room, agent))[0]['id']
                web.register_active_run(number, room, agent)
                web.run_agent_task(agent, room, 'fixture', number, number)
                row = self.sql('SELECT status,content FROM workbench_messages WHERE id=?', (number,))[0]
                return row, None
            service = CollaborationService(self.path, web.invoke_collaboration, web.cancel_collaboration)
            job = 'job-' + str(self.serial)
            value = dict(protocol_version=1, status='continue_work', questions=[],
                handoff={key: 'fixture' for key in HANDOFF_FIELDS},
                plan=dict(owner_id=agent, final_responder_id=agent, tasks=[
                    dict(task_id='work', agent_id=agent, instruction='fixture', depends_on=[])]))
            service.register_entry(job, room, agent, 'fixture', value, 'fixture')
            with service.store.transaction() as db:
                db.execute("INSERT INTO collab_calls(job_id,call_id,agent_id,role,state) VALUES (?,'test',?,'collaborator','reserved')", (job, agent))
            with patch.object(web, 'collaboration_service', return_value=service):
                result = web.invoke_collaboration(job, agent, 'collaborator', 'fixture', call_id='test')
            row = self.sql("SELECT content FROM workbench_messages WHERE id=?", (result.message_id,))[0]
            return dict(status=result.state, content=row['content']), result.dispatch_allowed

    def cli(self, *args):
        env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
        return subprocess.run([sys.executable, '-B', str(ROOT / 'inspect_execution.py'),
                               '--db', str(self.path), *args], cwd=ROOT, env=env,
                              capture_output=True, text=True, encoding='utf-8', timeout=15)

    def test_01_restart_retains(self):
        for index, state in enumerate(('reserved', 'running', 'quiescing')):
            token = self.begin('restart-' + str(index))
            if state == 'running':
                self.tracker.mark_running(token)
            if state == 'quiescing':
                self.tracker.finish_run(token, outcome='failed', exit_evidence='unknown')
        before = self.dump()
        web.initialize_workbench_tables()
        read_occupancy_snapshot(self.path, current_instance_id='new')
        self.assertEqual(self.dump(), before)

    def test_02_both_entries_block(self):
        for kind in ('ordinary', 'collaboration'):
            room = 'busy-' + kind
            token = self.begin(room)
            row, allowed = self.invoke(kind, False, room)
            self.assertEqual(row['status'], 'failed')
            self.assertIn('旧占用阻断', row['content'])
            self.assertFalse(allowed)
            entry = read_occupancy_snapshot(self.path, conversation_id=room)[0]
            self.assertEqual(entry['domain']['owner_run_id'], token.run_id)
        self.assertEqual(self.launched, [])

    def test_03_classifications(self):
        self.begin('old')
        self.begin('current', 'current')
        self.sql("INSERT INTO execution_sessions(agent_id,conversation_id,legacy_candidates_json) VALUES ('codex','candidate','[{\"session_id\":\"x\"}]')")
        old = self.begin('broken')
        self.sql('UPDATE execution_domains SET instance_id=? WHERE owner_run_id=?', ('wrong', old.run_id))
        rows = {r['conversation_id']:r for r in read_occupancy_snapshot(self.path, current_instance_id='current')}
        self.assertEqual(rows['old']['classification'], 'previous_instance_retained')
        self.assertEqual(rows['current']['classification'], 'current_instance_occupied')
        self.assertEqual(rows['broken']['classification'], 'record_inconsistent')
        self.assertEqual(rows['candidate']['classification'], 'session_needs_validation')
        self.assertFalse(rows['candidate']['blocked'])
        self.assertEqual(read_occupancy_snapshot(self.path, conversation_id='old')[0]['owner_relation'], 'unknown')

    def test_04_reverse_owners_and_legacy(self):
        self.legacy('legacy', 'old-id')
        self.sql("INSERT INTO execution_sessions(agent_id,conversation_id,owner_run_id,instance_id) VALUES ('codex','missing','absent','old')")
        entries = {e['conversation_id']:e for e in read_occupancy_snapshot(self.path)}
        self.assertTrue(entries['missing']['blocked'])
        self.assertEqual(entries['missing']['issues'][0]['run_id'], 'absent')
        self.assertEqual(entries['legacy']['legacy_bindings'][0]['session_id'], 'old-id')

    def test_05_times_and_evidence(self):
        token = self.begin('time')
        self.sql('UPDATE execution_runs SET cancel_requested=1,exit_confirmed=1 WHERE run_id=?', (token.run_id,))
        entry = read_occupancy_snapshot(self.path)[0]
        self.assertIsNone(entry['stage_updated_at'])
        self.assertEqual(entry['exit_status'], 'unknown')
        self.assertEqual(entry['runs'][0]['exit_confirmed'], 1)
        self.assertIn('非实时证明', inspect_cli.render_text([entry]))
        self.assertIn('查询时刻', inspect_cli.render_text([entry]))

    def test_06_cli_read_only_and_versions(self):
        self.begin('readonly')
        before = self.dump()
        for _ in range(2):
            result = self.cli('--json')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['entries'][0]['owner_relation'], 'unknown')
        self.assertEqual(self.dump(), before)
        missing = self.root / 'missing.db'
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'inspect_execution.py'), '--db', str(missing)], capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(missing.exists())
        self.sql('PRAGMA user_version=1')
        before = self.dump()
        result = self.cli('--json')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertEqual(self.dump(), before)

    def test_07_unrelated_entries_and_no_replay(self):
        self.begin('unrelated-busy')
        for kind in ('ordinary', 'collaboration'):
            for enabled in (False, True):
                for agent in ('codex', 'deepseek-harness'):
                    room = f'free-{kind}-{enabled}-{agent}'
                    before = len(self.launched)
                    row, allowed = self.invoke(kind, enabled, room, agent)
                    self.assertEqual(row['status'], 'completed', row['content'])
                    if kind == 'collaboration':
                        self.assertTrue(allowed)
                    self.assertEqual(len(self.launched), before + 1)
        self.assertEqual(len(self.launched), 8)

    def test_08_no_release_and_restart_message(self):
        result = self.cli('--help')
        self.assertEqual(result.returncode, 0)
        for arg in ('--release', '--force-clear', '--verify'):
            self.assertNotEqual(self.cli(arg).returncode, 0)
        for number in (101, 102):
            self.sql("INSERT INTO workbench_messages VALUES (?,'message','codex','user','','','pending')", (number,))
        self.begin('message', message=101)
        web.initialize_workbench_tables()
        rows = self.sql('SELECT * FROM workbench_messages ORDER BY id')
        self.assertTrue(all(r['status'] == 'cancelled' and '已停止' not in r['content'] for r in rows))
        self.assertIn('相关占用仍保留', rows[0]['content'])
        self.assertIn('未确认关联占用', rows[1]['content'])

    def test_09_orphan_runs_both_entries(self):
        for kind in ('ordinary', 'collaboration'):
            for variant in ('missing', 'idle', 'wrong_owner'):
                room = kind + variant
                token = self.begin(room)
                if variant == 'missing':
                    self.sql('DELETE FROM execution_domains WHERE conversation_id=?', (room,))
                elif variant == 'idle':
                    self.sql("UPDATE execution_domains SET state='idle',owner_run_id=NULL,instance_id=NULL WHERE conversation_id=?", (room,))
                else:
                    self.sql("UPDATE execution_domains SET owner_run_id='missing' WHERE conversation_id=?", (room,))
                count = len(self.sql('SELECT * FROM execution_runs'))
                row, _ = self.invoke(kind, False, room)
                self.assertEqual(row['status'], 'failed')
                self.assertEqual(len(self.sql('SELECT * FROM execution_runs')), count)
                entry = read_occupancy_snapshot(self.path, conversation_id=room)[0]
                self.assertIn('orphan_unfinished_run', [i['reason_code'] for i in entry['issues']])
                self.assertEqual(entry['runs'][0]['run_id'], token.run_id)
        self.assertFalse(self.launched)

    def test_10_all_residual_owner_states(self):
        for state in ('pending', 'running', 'quiescing', 'completed', 'failed', 'cancelled', 'unknown', 'missing', 'wrong_instance'):
            for legacy in (False, True):
                room = state + str(legacy)
                old = self.begin(room + '-owner')
                if state == 'missing':
                    self.sql('DELETE FROM execution_runs WHERE run_id=?', (old.run_id,))
                elif state != 'wrong_instance':
                    self.sql('UPDATE execution_runs SET state=? WHERE run_id=?', (state, old.run_id))
                self.sql("INSERT INTO execution_sessions(agent_id,conversation_id,owner_run_id,instance_id) VALUES ('codex',?,?,?)",
                         (room, old.run_id, 'wrong' if state == 'wrong_instance' else 'old'))
                token = self.begin(room, 'current')
                before = self.sql('SELECT * FROM execution_sessions WHERE conversation_id=?', (room,))
                with self.assertRaises(DomainBusy):
                    if legacy:
                        self.tracker.select_legacy_session(token, 'codex')
                    else:
                        self.tracker.claim_session(token, self.identity())
                self.assertEqual(self.sql('SELECT * FROM execution_sessions WHERE conversation_id=?', (room,)), before)

    def test_11_real_transaction_competition(self):
        # First schedule: first holds selected execution while second selects.
        # Second schedule: both reserve before selection; both may reject.
        for both_reserved in (False, True):
            prefix = str(both_reserved)
            for suffix in ('a', 'b'):
                self.legacy(prefix + suffix, prefix + '-shared')
            selected = threading.Event()
            second_done = threading.Event()
            barrier = threading.Barrier(2)
            selections_done = threading.Barrier(2)
            launches, failures = [], []
            def contender(suffix):
                tracker = ExecutionTracker(self.path)  # independent native connection per txn
                if not both_reserved and suffix == 'b':
                    if not selected.wait(5):
                        failures.append(AssertionError('first selection timed out'))
                        return
                token = self.begin(prefix + suffix, 'current')
                try:
                    if both_reserved:
                        barrier.wait(5)
                    try:
                        sid = tracker.select_legacy_session(token, 'codex')
                        launches.append((suffix, sid))
                    except DomainBusy:
                        pass
                    if suffix == 'a':
                        selected.set()
                    else:
                        second_done.set()
                    # Keep both domain reservations alive until both SELECT
                    # admission decisions are complete; no sequential reuse.
                    selections_done.wait(5)
                except BaseException as error:
                    failures.append(error)
                    selected.set()
                    second_done.set()
                finally:
                    tracker.finish_run(token, outcome='failed', exit_evidence='not_started')
            threads = [threading.Thread(target=contender, args=(suffix,)) for suffix in ('a', 'b')]
            for t in threads:
                t.start()
            for t in threads:
                t.join(12)
                self.assertFalse(t.is_alive())
            self.assertFalse(failures, failures)
            self.assertLessEqual(len(launches), 1, launches)
            if not both_reserved:
                self.assertEqual(len(launches), 1)

    def test_12_fixed_id_after_binding_change(self):
        for agent in ('codex', 'deepseek-harness'):
            room = 'fixed-' + agent
            self.legacy(room, 'checked-id', agent)
            original = web.prepare_execution_session
            def select(*args):
                sid = original(*args)
                self.legacy(room, 'changed-id', agent)
                return sid
            with patch.object(web, 'prepare_execution_session', side_effect=select):
                row, _ = self.invoke('ordinary', False, room, agent)
            self.assertEqual(row['status'], 'completed', row['content'])
            command = self.launched[-1]
            self.assertIn('checked-id', command)
            self.assertNotIn('changed-id', command)

    def test_13_candidate_semantics_both_paths(self):
        for kind in ('ordinary', 'collaboration'):
            for enabled in (False, True):
                room = 'candidate-' + kind + str(enabled)
                self.legacy(room, 'candidate-id')
                before = len(self.launched)
                row, allowed = self.invoke(kind, enabled, room)
                self.assertEqual(row['status'], 'failed' if enabled else 'completed', row['content'])
                self.assertEqual(len(self.launched), before if enabled else before + 1)
                if enabled:
                    self.assertIn('explicit candidate adoption', row['content'])
                    self.assertIsNone(self.sql('SELECT owner_run_id FROM execution_sessions WHERE conversation_id=?', (room,))[0]['owner_run_id'])

    def test_14_wal_barrier_and_query_failures(self):
        with closing(sqlite3.connect(self.path)) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute('PRAGMA wal_autocheckpoint=0')
            writer.execute("INSERT INTO execution_sessions(agent_id,conversation_id) VALUES ('codex','wal-visible')")
            writer.commit()
            self.assertTrue(Path(str(self.path) + '-wal').stat().st_size > 32)
            result = self.cli('--conversation', 'wal-visible', '--json')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['entries'][0]['conversation_id'], 'wal-visible')
        code = ('import sys; from workbench.execution.migration_barrier import MigrationBarrier; '
                'b=MigrationBarrier(sys.argv[1],exclusive=True); b.__enter__(); '
                'print("locked",flush=True); sys.stdin.readline(); b.__exit__()')
        with subprocess.Popen([sys.executable, '-B', '-c', code, str(self.path)], cwd=ROOT,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True) as holder:
            try:
                self.assertEqual(holder.stdout.readline().strip(), 'locked')
                result = self.cli('--json')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')
            finally:
                holder.communicate('\n', timeout=10)
        self.sql("UPDATE execution_sessions SET legacy_candidates_json='broken' WHERE conversation_id='wal-visible'")
        result = self.cli('--json')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        with patch('sqlite3.connect', side_effect=sqlite3.OperationalError('injected')):
            out, err = StringIO(), StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(inspect_cli.main(['--db', str(self.path)]), 1)
            self.assertEqual(out.getvalue(), '')
            self.assertIn('查询失败', err.getvalue())

    def test_15_scopes_and_visible_associations(self):
        self.ready('scoped', 'same', scope='backend-a')
        old = self.begin('scoped')
        target = self.begin('target', 'current')
        with closing(sqlite3.connect(self.path)) as db:
            self.assertTrue(find_session_conflicts(db, agent_id='codex', conversation_id='target',
                adapter_id='codex', session_id='same', backend_scope='backend-a',
                current_run_id=target.run_id, current_instance_id='current'))
            self.assertFalse(find_session_conflicts(db, agent_id='codex', conversation_id='target',
                adapter_id='codex', session_id='same', backend_scope='backend-b'))
            self.assertFalse(find_session_conflicts(db, agent_id='deepseek-harness', conversation_id='target',
                adapter_id='dsh', session_id='same'))
        self.legacy('legacy-busy', 'legacy-shared')
        self.begin('legacy-busy')
        self.legacy('target', 'legacy-shared')
        with self.assertRaises(DomainBusy):
            self.tracker.select_legacy_session(target, 'codex')
        self.legacy('unknown-free', 'no-collision')
        free = self.begin('unknown-free', 'current')
        self.assertEqual(self.tracker.select_legacy_session(free, 'codex'), 'no-collision')
        entry = read_occupancy_snapshot(self.path, conversation_id='unknown-free')[0]
        self.assertEqual(entry['legacy_bindings'][0]['scope_relation'], 'scope_unknown')

    def test_16_refusal_cleanup_and_failure(self):
        for kind in ('ordinary', 'collaboration'):
            for enabled in (False, True):
                for fault in ('owner', 'shared', 'query', 'finish'):
                    room = f'refuse-{kind}-{enabled}-{fault}'
                    if enabled:
                        self.ready(room, room + '-id')
                    else:
                        self.legacy(room, room + '-id')
                    if fault == 'owner':
                        if not enabled:
                            self.sql("INSERT INTO execution_sessions(agent_id,conversation_id) VALUES ('codex',?)", (room,))
                        self.sql("UPDATE execution_sessions SET owner_run_id='absent',instance_id='old' WHERE conversation_id=?", (room,))
                    elif fault == 'shared':
                        self.legacy(room + '-old', room + '-id')
                        self.begin(room + '-old')
                    old_sessions = self.sql('SELECT * FROM execution_sessions WHERE conversation_id=?', (room,))
                    old_domains = self.sql('SELECT * FROM execution_domains WHERE conversation_id=?', (room + '-old',))
                    count = len(self.launched)
                    with ExitStack() as stack:
                        if fault in ('query', 'finish'):
                            method = 'claim_session' if enabled else 'select_legacy_session'
                            stack.enter_context(patch.object(ExecutionTracker, method, side_effect=sqlite3.OperationalError('injected query')))
                        if fault == 'finish':
                            # Fail the actual domain UPDATE after the run UPDATE;
                            # the real tracker transaction must roll both back.
                            self.sql("CREATE TRIGGER reject_fixture_finish BEFORE UPDATE OF state ON execution_domains "
                                     "WHEN NEW.state='idle' AND NEW.conversation_id='" + room + "' "
                                     "BEGIN SELECT RAISE(ABORT,'injected finish'); END")
                        try:
                            row, allowed = self.invoke(kind, enabled, room)
                        finally:
                            if fault == 'finish':
                                self.sql('DROP TRIGGER reject_fixture_finish')
                    self.assertEqual(row['status'], 'failed', row)
                    self.assertEqual(len(self.launched), count)
                    if kind == 'collaboration':
                        self.assertFalse(allowed)
                    current = self.sql('SELECT * FROM execution_runs WHERE conversation_id=? ORDER BY rowid DESC LIMIT 1', (room,))[0]
                    domain = self.sql('SELECT * FROM execution_domains WHERE conversation_id=?', (room,))[0]
                    self.assertEqual(current['state'], 'pending' if fault == 'finish' else 'failed')
                    self.assertEqual(domain['state'], 'reserved' if fault == 'finish' else 'idle')
                    self.assertEqual(self.sql('SELECT * FROM execution_sessions WHERE conversation_id=?', (room,)), old_sessions)
                    self.assertEqual(self.sql('SELECT * FROM execution_domains WHERE conversation_id=?', (room + '-old',)), old_domains)
                    if fault == 'finish':
                        self.assertIn('执行域未释放', row['content'])

    def test_17_reason_codes_and_messages(self):
        cases = [(DomainBusy('private', reason_code='session_owner_retained'), '旧占用阻断'),
                 (DomainBusy('private', reason_code='shared_session_occupied', records=[dict(category='concurrent_rejection')]), '本次并发拒绝'),
                 (InspectionQueryError('private'), '查询失败')]
        for error, label in cases:
            formatted = format_execution_error(ExecutionAdmissionError(error))
            self.assertIn(label, formatted)
            self.assertNotIn('private', formatted)
            self.assertNotIn('无占用', formatted)


class NumberedResult(unittest.TextTestResult):
    def addSuccess(self, test):
        super().addSuccess(test)
        print('PASS ' + test._testMethodName, flush=True)

    def addFailure(self, test, err):
        super().addFailure(test, err)
        print('FAIL ' + test._testMethodName + ': ' + str(err[1]), flush=True)

    def addError(self, test, err):
        super().addError(test, err)
        print('FAIL ' + test._testMethodName + ': ' + repr(err[1]), flush=True)


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)  # injected failures are expected and asserted
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Acceptance)
    result = unittest.TextTestRunner(verbosity=0, resultclass=NumberedResult).run(suite)
    print(f'SUMMARY: {result.testsRun-len(result.failures)-len(result.errors)}/17 PASS; '
          f'{len(result.failures)+len(result.errors)} FAIL; synthetic transport only')
    raise SystemExit(0 if result.wasSuccessful() else 1)
