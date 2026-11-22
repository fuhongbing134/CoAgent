"""Isolated data tests. 'confirmed' is simulated, never real process evidence."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import json
import sqlite3
import tempfile
import threading
import unittest

from workbench.storage.database_schema import ensure_schema
from workbench.execution.execution_tracking import DomainBusy, ExecutionTracker, StaleRun


class ExecutionTrackingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'test.db'
        ensure_schema(self.path)
        self.tracker = ExecutionTracker(self.path)

    def begin(self, **changes):
        args = dict(agent_id='codex', conversation_id='room', instance_id='test-instance',
                    source_kind='collaboration', config_snapshot={'mode': 'resume'},
                    source_job_id='job', source_call_id='call', source_message_id=7)
        args.update(changes)
        return self.tracker.begin_run(**args)

    def rows(self, table):
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute('SELECT * FROM ' + table)]

    def test_complete_and_repeat(self):
        token = self.begin()
        run = self.rows('execution_runs')[0]
        self.assertEqual((run['source_job_id'], run['source_call_id'], run['source_message_id']),
                         ('job', 'call', 7))
        self.assertEqual(token.fence, 1)
        self.assertTrue(self.tracker.mark_running(token))
        self.assertFalse(self.tracker.mark_running(token))
        self.assertTrue(self.tracker.finish_run(token, outcome='completed', exit_evidence='confirmed'))
        self.assertFalse(self.tracker.finish_run(token, outcome='completed', exit_evidence='confirmed'))
        self.assertEqual(self.rows('execution_runs')[0]['exit_confirmed'], 1)
        domain = self.rows('execution_domains')[0]
        self.assertEqual((domain['state'], domain['owner_run_id'], domain['instance_id']), ('idle', None, None))
        with self.assertRaises(ValueError):
            self.tracker.finish_run(token, outcome='failed')

    def test_grok_diagnostic_remains_bound_to_its_message_and_run(self):
        token = self.begin(agent_id='grok', source_kind='workbench',
                           source_job_id=None, source_call_id=None, source_message_id=17)
        other = self.begin()
        self.tracker.set_launch_snapshot(token, {'launch_mode': 'new'})
        self.tracker.mark_running(token)
        summary = {'exit_code': 0, 'server_events': ['started']}
        self.tracker.record_grok_diagnostic(token, summary)
        self.tracker.finish_run(token, outcome='completed', exit_evidence='confirmed')
        rows = {row['run_id']: row for row in self.rows('execution_runs')}
        self.assertEqual(rows[token.run_id]['source_message_id'], 17)
        self.assertEqual(json.loads(rows[token.run_id]['config_snapshot']),
                         {'launch_mode': 'new', 'grok_diagnostic': summary})
        self.assertNotIn('grok_diagnostic', json.loads(rows[other.run_id]['config_snapshot']))

    def test_unknown_retains_domain_until_confirmed(self):
        token = self.begin()
        self.tracker.mark_running(token)
        self.tracker.finish_run(token, outcome='failed')
        self.assertEqual(self.rows('execution_runs')[0]['state'], 'quiescing')
        self.assertEqual(self.rows('execution_runs')[0]['exit_confirmed'], 0)
        self.assertEqual(self.rows('execution_domains')[0]['owner_run_id'], token.run_id)
        with self.assertRaises(DomainBusy):
            self.begin()
        with self.assertRaises(ValueError):
            self.tracker.finish_run(token, outcome='failed', exit_evidence='not_started')
        self.tracker.finish_run(token, outcome='failed', exit_evidence='confirmed')
        self.assertEqual(self.rows('execution_domains')[0]['state'], 'idle')

    def test_prestart_failure_and_cancel(self):
        token = self.begin()
        self.tracker.finish_run(token, outcome='failed', exit_evidence='not_started')
        self.assertEqual(self.rows('execution_runs')[0]['exit_confirmed'], 0)
        token = self.begin()
        self.assertTrue(self.tracker.request_cancel(token))
        self.assertFalse(self.tracker.request_cancel(token))
        with self.assertRaises(ValueError):
            self.tracker.mark_running(token)
        self.tracker.finish_run(token, outcome='failed', exit_evidence='not_started')
        self.assertEqual(self.rows('execution_runs')[1]['state'], 'cancelled')

    def test_running_cancel_does_not_claim_exit(self):
        token = self.begin()
        self.tracker.mark_running(token)
        self.tracker.request_cancel(token)
        self.assertEqual(self.rows('execution_runs')[0]['state'], 'running')
        self.tracker.finish_run(token, outcome='completed')
        self.assertEqual(self.rows('execution_runs')[0]['state'], 'quiescing')
        self.tracker.finish_run(token, outcome='completed', exit_evidence='confirmed')
        self.assertEqual(self.rows('execution_runs')[0]['state'], 'cancelled')

    def test_bad_tokens_leave_everything_unchanged(self):
        token = self.begin()
        before = self.rows('execution_runs'), self.rows('execution_domains')
        for bad in (replace(token, instance_id='other'), replace(token, run_id='other'),
                    replace(token, fence=2), replace(token, fence=True)):
            for operation in (self.tracker.mark_running, self.tracker.request_cancel,
                              lambda t: self.tracker.finish_run(t, outcome='failed')):
                with self.subTest(token=bad, operation=operation), self.assertRaises(StaleRun):
                    operation(bad)
                self.assertEqual((self.rows('execution_runs'), self.rows('execution_domains')), before)

    def test_old_callback_cannot_release_new_owner(self):
        old = self.begin()
        self.tracker.finish_run(old, outcome='failed', exit_evidence='not_started')
        new = self.begin()
        self.assertEqual(new.fence, old.fence + 1)
        with self.assertRaises(StaleRun):
            self.tracker.finish_run(old, outcome='failed', exit_evidence='confirmed')
        self.assertEqual(self.rows('execution_domains')[0]['owner_run_id'], new.run_id)

    def test_first_and_existing_domain_competition(self):
        for existing in (False, True):
            room = str(existing)
            if existing:
                old = self.begin(conversation_id=room)
                self.tracker.finish_run(old, outcome='failed', exit_evidence='not_started')
            barrier = threading.Barrier(2)

            def compete():
                barrier.wait(timeout=5)
                try:
                    return self.begin(conversation_id=room)
                except DomainBusy:
                    return None

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(compete) for _ in range(2)]
                results = [f.result(timeout=15) for f in futures]
            self.assertEqual(sum(r is not None for r in results), 1)
            self.assertEqual(len([r for r in self.rows('execution_runs')
                                  if r['conversation_id'] == room]), 2 if existing else 1)

    def test_reservation_and_finish_rollback_together(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("""CREATE TRIGGER fail_reserve BEFORE UPDATE ON execution_domains
                WHEN NEW.state='reserved' BEGIN SELECT RAISE(ABORT,'injected'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'injected'):
            self.begin()
        self.assertEqual(self.rows('execution_runs'), [])
        self.assertEqual(self.rows('execution_domains'), [])
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DROP TRIGGER fail_reserve')
        token = self.begin()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("""CREATE TRIGGER fail_release BEFORE UPDATE ON execution_domains
                WHEN NEW.state='idle' BEGIN SELECT RAISE(ABORT,'injected'); END""")
        before = self.rows('execution_runs'), self.rows('execution_domains')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'injected'):
            self.tracker.finish_run(token, outcome='failed', exit_evidence='not_started')
        self.assertEqual((self.rows('execution_runs'), self.rows('execution_domains')), before)

    def test_domains_are_independent_and_sessions_untouched(self):
        first = self.begin()
        self.begin(conversation_id='another')
        self.begin(agent_id='deepseek-harness')
        with self.assertRaises(DomainBusy):
            self.begin()
        self.assertEqual(len(self.rows('execution_runs')), 3)
        self.assertEqual(self.rows('execution_sessions'), [])
        self.assertEqual(self.rows('execution_domains')[0]['owner_run_id'], first.run_id)

    def test_no_implicit_initialization(self):
        missing = Path(self.temp.name) / 'missing.db'
        with self.assertRaises(sqlite3.OperationalError):
            ExecutionTracker(missing).begin_run(agent_id='a', conversation_id='c', instance_id='i',
                                               source_kind='workbench', config_snapshot={})
        self.assertFalse(missing.exists())



if __name__ == '__main__':
    unittest.main()
