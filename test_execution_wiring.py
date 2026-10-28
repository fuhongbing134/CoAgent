"""4B isolated integration. Scripted CLI output is NOT real backend acceptance."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from collaboration_service import CollaborationService, InvocationResult
from execution_runtime import Execution
from execution_tracking import DomainBusy, ExecutionTracker
from managed_process import ProcessResult
from test_collaboration_foundation import payload
import web_chat as web


class WiringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'isolated.db'
        for name, value in [('DB_PATH', self.path), ('SERVICE_INSTANCE_ID', 'test-instance')]:
            p = patch.object(web, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.path.touch()
        web.initialize_workbench_tables()
        self.addCleanup(self.clear_registry)

    def clear_registry(self):
        with web.ACTIVE_RUNS_LOCK:
            web.ACTIVE_RUNS.clear()
            web.COLLAB_MESSAGES.clear()

    def query(self, sql, args=()):
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(sql, args)]

    def message(self, number=1, agent='codex'):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO workbench_messages(id,conversation_id,sender_id,recipient_id,content,created_at,status) VALUES (?,'chat',?,'user','','','pending')", (number, agent))
        web.register_active_run(number, 'chat', agent)

    def execution(self, number=1):
        self.message(number)
        return web.begin_execution(number, source_kind='workbench')

    def test_two_entries_busy_and_restart_never_takes_over(self):
        first = self.execution()
        self.message(2)
        with self.assertRaises(DomainBusy):
            web.begin_execution(2, source_kind='collaboration', job_id='j', call_id='c')
        with patch.object(web, 'SERVICE_INSTANCE_ID', 'new-instance'):
            self.message(3)
            with self.assertRaises(DomainBusy):
                web.begin_execution(3, source_kind='workbench')
        self.assertEqual(len(self.query('SELECT * FROM execution_runs')), 1)
        self.assertEqual(self.query('SELECT * FROM execution_domains')[0]['owner_run_id'], first.token.run_id)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows Job integration')
    def test_real_process_release_and_lock_order_during_cancel(self):
        execution = self.execution()
        seen = threading.Event()
        result = []
        def launch():
            result.append(execution.launch([sys.executable, '-c', 'import time; time.sleep(30)'],
                snapshot={}, timeout=10, on_created=lambda owner: seen.set()))
        # on_created is a test observer; normal runtime registration precedes creation.
        worker = threading.Thread(target=launch)
        worker.start()
        self.addCleanup(lambda: worker.join(15))
        self.assertTrue(seen.wait(5))
        stop = threading.Thread(target=lambda: web.stop_active_run(1))
        stop.start()
        # Neither cancellation nor DB writes can wait while holding registry lock.
        self.assertTrue(web.ACTIVE_RUNS_LOCK.acquire(timeout=1))
        web.ACTIVE_RUNS_LOCK.release()
        stop.join(5)
        worker.join(8)
        self.assertFalse(stop.is_alive())
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0].exit_evidence, 'confirmed')
        self.assertTrue(web.finish_execution(1))
        self.assertEqual(self.query('SELECT state FROM execution_runs')[0]['state'], 'cancelled')
        self.assertEqual(self.query('SELECT state FROM execution_domains')[0]['state'], 'idle')

    def test_cancel_before_start_never_launches(self):
        self.execution()
        web.stop_active_run(1)
        with patch('execution_runtime.ManagedProcess.run') as launch:
            with self.assertRaises(ValueError):
                web.run_managed_process(1, [sys.executable, '--sandbox', 'read-only'])
        launch.assert_not_called()
        self.assertTrue(web.finish_execution(1))

    def test_unknown_exit_keeps_domain_and_prevents_cancel_ack(self):
        execution = self.execution()
        execution.result = ProcessResult('failed', 0, b'', b'', 'unknown')
        self.assertFalse(web.finish_execution(1))
        self.assertEqual(self.query('SELECT state FROM execution_domains')[0]['state'], 'quiescing')
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE execution_runs SET source_job_id='job'")
        self.assertFalse(web.cancel_collaboration('job'))
        self.assertIn('执行域未释放', self.query('SELECT content FROM workbench_messages')[0]['content'])

    def test_release_failure_preserves_owner_and_is_visible(self):
        execution = self.execution()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TRIGGER reject_release BEFORE UPDATE OF state ON execution_domains WHEN NEW.state='idle' BEGIN SELECT RAISE(ABORT,'release fault'); END")
        self.assertFalse(web.finish_execution(1))
        self.assertEqual(self.query('SELECT state FROM execution_domains')[0]['state'], 'reserved')
        self.assertEqual(self.query('SELECT status FROM workbench_messages')[0]['status'], 'failed')
        self.assertFalse(execution.released)

    def test_service_stops_same_drive_and_retains_budget(self):
        calls = []
        def invoke(*args, **kwargs):
            calls.append(kwargs['call_id'])
            return InvocationResult('failed', 'release fault', False)
        service = CollaborationService(self.path, invoke, lambda job: False)
        item = payload()
        item['status'] = 'request_collaboration'
        service.register_entry('job','chat','codex','work',item,'Plan\n@DeepSeek Harness')
        service.drive('job')
        self.assertEqual(len(calls), 1)
        self.assertEqual(service.store.snapshot('job')['used'], 2)
        self.assertEqual(service.store.snapshot('job')['phase'], 'failed')
        self.assertEqual(self.query("SELECT state FROM collab_calls WHERE call_id=?", (calls[0],))[0]['state'], 'failed')
        service.drive('job')
        self.assertEqual(len(calls), 1)

    def test_cancel_unconfirmed_event_remains_pending(self):
        service = CollaborationService(self.path, lambda *a, **kw: None, lambda job: False)
        item = payload()
        item['status'] = 'continue_work'
        service.register_entry('job','chat','codex','work',item,'Plan')
        service.store.operate('job', 'chat', 'stop', 'stop')
        service.drive('job')
        self.assertEqual(self.query('SELECT phase FROM collab_lifecycle')[0]['phase'], 'quiescing')
        self.assertEqual(self.query('SELECT state FROM collab_events')[0]['state'], 'pending')

    def test_actual_workers_new_resume_single_run_and_final_policy(self):
        # Fake protocol responses exercise real worker command construction and
        # real SQLite tracking. confirmed here is injected, not CLI evidence.
        launched = []
        def native_run(owner, command, **kwargs):
            kwargs['on_running'](owner)
            launched.append(command)
            if '--output-last-message' in command:
                Path(command[command.index('--output-last-message')+1]).write_text('answer', encoding='utf-8')
                output = b'{"type":"thread.started","thread_id":"thread-fixture"}\n'
            else:
                output = b'{"type":"session","sessionId":"session-fixture"}\n{"type":"final","text":"answer"}\n'
            return ProcessResult('completed', 0, output, b'', 'confirmed')
        patch_file = self.root / 'patch.yml'
        patch_file.write_text('{}', encoding='utf-8')
        # The worker reads policy from its actual workspace, not the workbench root.
        (self.root / 'AGENTS.md').write_text('fixture entry', encoding='utf-8')
        (self.root / 'specs').mkdir()
        (self.root / 'specs' / 'README.md').write_text('fixture index', encoding='utf-8')
        with patch.object(web, 'resolve_working_directory', return_value=self.root), \
             patch.object(web, 'resolve_codex_command', return_value=sys.executable), \
             patch.object(web, 'resolve_dsh_command', return_value=sys.executable), \
             patch.object(web, 'DSH_AUTH_PATCH', patch_file), \
             patch('execution_runtime.ManagedProcess.run', native_run):
            for index, agent in enumerate(('codex','codex','deepseek-harness','deepseek-harness'), 1):
                self.message(index, agent)
                web.COLLAB_CONTEXT.final_delivery = True
                try:
                    web.run_agent_task(agent, 'chat', 'hello', index, 0)
                finally:
                    web.COLLAB_CONTEXT.final_delivery = False
        rows = self.query('SELECT * FROM execution_runs ORDER BY rowid')
        self.assertEqual(len(rows), 4)
        self.assertEqual([json.loads(r['config_snapshot'])['launch_mode'] for r in rows], ['new','resume','new','resume'])
        self.assertTrue(all(r['state'] == 'completed' and r['source_kind']=='workbench' for r in rows))
        self.assertEqual(launched[0][launched[0].index('--sandbox')+1], 'read-only')
        self.assertEqual(self.query('SELECT * FROM execution_sessions'), [])
        self.assertEqual(json.loads(rows[-1]['config_snapshot'])['profile'], web.DSH_PROFILE)

    def test_real_invoke_roles_and_replan_keep_actual_call_identity(self):
        service = CollaborationService(self.path, web.invoke_collaboration, web.cancel_collaboration)
        item = payload()
        item['status'] = 'continue_work'
        service.register_entry('job','chat','codex','work',item,'Plan')
        observed = []
        def worker(conversation, prompt, message, user):
            run = web.active_run(message)
            execution = run['execution']
            observed.append(getattr(web.COLLAB_CONTEXT, 'final_delivery', False))
            execution.result = ProcessResult('completed', 0, b'', b'', 'confirmed')
            result = payload()
            result.pop('plan')
            text = 'Answer\n```agent-collaboration\n' + json.dumps(result) + '\n```'
            web.update_workbench_message(message, text, 'completed')
            return web.WorkerResult('completed', text, execution.result)
        with patch.object(web, 'collaboration_service', return_value=service), \
             patch.object(web, 'run_codex', worker):
            for index, role in enumerate(('collaborator','owner','final')):
                call_id = 'actual-' + str(index)
                # Transport-only test; reserve the real call row without driving
                # lifecycle to final_delivery, which is separately protocol-tested.
                with service.store.transaction() as db:
                    db.execute("INSERT INTO collab_calls(job_id,call_id,agent_id,role,state) VALUES (?,?,? ,?,'reserved')",
                               ('job', call_id, 'codex', role))
                reply = web.invoke_collaboration('job','codex',role,'prompt',call_id=call_id)
                self.assertTrue(reply.dispatch_allowed)
                duplicate = web.invoke_collaboration('job','codex',role,'prompt',call_id=call_id)
                self.assertTrue(duplicate.dispatch_allowed)
        rows = self.query('SELECT source_kind,source_job_id,source_call_id,state FROM execution_runs ORDER BY rowid')
        self.assertEqual([r['source_call_id'] for r in rows], ['actual-0','actual-1','actual-2'])
        self.assertTrue(all(r['source_kind']=='collaboration' and r['source_job_id']=='job' for r in rows))
        self.assertEqual(observed, [False,False,True])
        # Replan directly invokes transport; verify it receives the reserved ID.
        from test_direct_answer import question, answer, envelope
        captured = []
        def replan_transport(job, agent, role, prompt, **kwargs):
            call = self.query('SELECT state FROM collab_calls WHERE call_id=?', (kwargs['call_id'],))
            self.assertEqual(call[0]['state'], 'reserved')
            self.assertTrue(kwargs['entry_mode'])
            captured.append(kwargs['call_id'])
            return InvocationResult('completed', envelope(answer()), True)
        other = CollaborationService(self.path, replan_transport, lambda job: True)
        other.register_entry('clarify','chat','codex','work',question(),envelope(question()))
        other.store.operate('clarify','chat','answer','answer','scope')
        other.drive('clarify')
        self.assertEqual(len(captured), 1)
        self.assertEqual(other.store.snapshot('clarify')['phase'], 'completed')

    def test_entry_release_before_kick_and_duplicate_cannot_reexecute(self):
        from test_direct_answer import scripted_worker, envelope
        self.message()
        service = CollaborationService(self.path, lambda *a, **kw: None, lambda job: True)
        item = payload()
        item['status'] = 'continue_work'
        def worker(*args):
            web.update_codex_message(1, envelope(item), 'completed')
        observed = []
        def kick(job):
            observed.append(self.query('SELECT state FROM execution_domains')[0]['state'])
        with patch.object(web, 'run_codex', scripted_worker(worker)) as launch, \
             patch.object(web, 'collaboration_service', return_value=service), \
             patch.object(service, 'kick', kick):
            web.run_agent_task('codex','chat','@Codex work',1,0)
            web.run_agent_task('codex','chat','@Codex work',1,0)
        self.assertEqual(observed, ['idle'])
        self.assertEqual(len(self.query('SELECT * FROM execution_runs')), 1)

    def test_interrupt_running_does_not_clear_domain(self):
        self.execution()
        service = CollaborationService(self.path, lambda *a, **kw: None, lambda job: True)
        before = self.query('SELECT * FROM execution_domains')
        service.store.interrupt_running()
        self.assertEqual(self.query('SELECT * FROM execution_domains'), before)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows Job integration')
    def test_running_write_failure_cleans_suspended_process(self):
        execution = self.execution()
        marker = self.root / 'must-not-exist'
        command = [sys.executable, '-c', 'from pathlib import Path; Path(' + repr(str(marker)) + ').touch()']
        with patch.object(execution.tracker, 'mark_running', side_effect=sqlite3.OperationalError('running fault')):
            result = execution.launch(command, snapshot={}, timeout=3)
        self.assertEqual(result.outcome, 'failed')
        self.assertEqual(result.exit_evidence, 'confirmed')
        self.assertFalse(marker.exists())
        self.assertTrue(web.finish_execution(1))
        self.assertEqual(self.query('SELECT state FROM execution_runs')[0]['state'], 'failed')

    def test_busy_actual_transport_fails_reserved_call_without_launch_or_refund(self):
        from collaboration_service import DispatchHalted
        self.execution()
        service = CollaborationService(self.path, web.invoke_collaboration, web.cancel_collaboration)
        item = payload()
        item['status'] = 'continue_work'
        service.register_entry('job','chat','codex','work',item,'Plan')
        with patch.object(web, 'collaboration_service', return_value=service), \
             patch.object(web, 'run_codex') as launch:
            with self.assertRaises(DispatchHalted):
                service.call('job', 'codex', 'collaborator', 'work')
        launch.assert_not_called()
        self.assertEqual(service.store.snapshot('job')['used'], 2)
        self.assertEqual(self.query("SELECT state FROM collab_calls WHERE role='collaborator'")[0]['state'], 'failed')
        self.assertEqual(len(self.query('SELECT * FROM execution_runs')), 1)

    def test_service_identity_exists_only_inside_startup_instance_lock(self):
        with patch.object(web, 'COLLAB_SERVICE', None), patch.object(web, 'SERVICE_INSTANCE_ID', None):
            with web.startup_server(0):
                first = web.SERVICE_INSTANCE_ID
                self.assertEqual(len(first), 32)
                self.assertEqual(web.SERVICE_INSTANCE_ID, first)
            self.assertIsNone(web.SERVICE_INSTANCE_ID)
            with web.startup_server(0):
                self.assertNotEqual(web.SERVICE_INSTANCE_ID, first)


if __name__ == '__main__':
    unittest.main()
