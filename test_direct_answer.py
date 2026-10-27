"""Isolated SQLite integration tests with scripted transport, not live CLI evidence."""
import json
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import web_chat as web
from collaboration_entry import parse_entry_response
from collaboration_protocol import validate_payload
from managed_process import ProcessResult
from collaboration_service import CollaborationService
from test_collaboration_foundation import payload


def scripted_worker(worker):
    # Protocol/persistence fixture only: injected evidence is NOT real CLI proof.
    def wrapped(*args):
        execution = web.active_run(args[2])['execution']
        execution.result = ProcessResult('completed', 0, b'', b'', 'confirmed')
        return web.structured_worker(worker)(*args)
    return wrapped


def envelope(item):
    return 'Answer\n```agent-collaboration\n' + json.dumps(item) + '\n```'


def answer(final='codex'):
    item = payload()
    item['plan'].update(final_responder_id=final, tasks=[])
    return item


def question():
    item = payload()
    item.pop('plan')
    item.update(status='waiting_user', questions=['Which scope?'])
    return item


class DirectAnswerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'isolated.db'

    def service(self, invoke):
        return CollaborationService(self.path, invoke, lambda job: True)

    def clarify(self, service):
        item = question()
        service.register_entry('job', 'chat', 'codex', 'Request', item, envelope(item))
        service.store.operate('job', 'chat', 'a1', 'answer', 'Scope clarified')

    def test_entry_plan_requirement_unchanged(self):
        self.assertEqual(parse_entry_response(envelope(answer()))[1]['plan']['tasks'], [])
        missing = answer()
        del missing['plan']
        with self.assertRaisesRegex(ValueError, 'initial plan required'):
            parse_entry_response(envelope(missing))
        nonempty = payload()
        del nonempty['plan']['tasks'][0]['depends_on']
        with self.assertRaisesRegex(ValueError, 'planning-only'):
            parse_entry_response(envelope(nonempty))

    def test_contract_and_registration_defense(self):
        self.assertEqual(parse_entry_response(envelope(answer()))[1]['plan']['tasks'], [])
        with self.assertRaises(ValueError):
            validate_payload(answer(), role='owner')
        for status in ('continue_work', 'request_collaboration'):
            item = answer()
            item['status'] = status
            with self.assertRaises(ValueError):
                validate_payload(item, role='entry')
        item = payload()
        with self.assertRaises(ValueError):
            validate_payload(item, role='entry')
        item['status'] = 'continue_work'
        item['plan']['tasks'][0]['task_id'] = '__clarify_entry__'
        with self.assertRaises(ValueError):
            validate_payload(item, role='entry')
        service = self.service(lambda *a, **kw: self.fail('must not invoke'))
        with self.assertRaises(ValueError):
            service.register_entry('job', 'chat', 'codex', 'Request', answer(), envelope(answer()))
        with service.store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM collab_jobs').fetchone()[0], 0)

    def test_clarification_direct_same_or_different_final(self):
        for final in ('codex', 'deepseek-harness'):
            with self.subTest(final=final), tempfile.TemporaryDirectory() as root:
                calls = []
                def invoke(job, agent, role, prompt, **kwargs):
                    calls.append((agent, role))
                    item = answer(final)
                    if role == 'final':
                        item.pop('plan')
                    return 'completed', envelope(item)
                service = CollaborationService(Path(root)/'db', invoke, lambda job: True)
                self.clarify(service)
                service.drive('job')
                self.assertEqual(service.store.snapshot('job')['phase'], 'completed')
                self.assertIn('Answer', service.store.snapshot('job')['final_text'])
                tasks = service.material('job')[1]
                self.assertEqual(len(tasks), 1)
                self.assertEqual(tasks[0]['state'], 'completed')
                self.assertIn('澄清已解决', tasks[0]['result'])
                self.assertEqual(calls, [('codex', 'owner')] + ([] if final == 'codex' else [(final, 'final')]))
                service.drive('job')
                self.assertEqual(len(calls), 1 if final == 'codex' else 2)

    def test_normal_replan_still_executes_task(self):
        roles = []
        def invoke(job, agent, role, prompt, **kwargs):
            roles.append(role)
            item = payload()
            if len(roles) == 1:
                item['status'] = 'continue_work'
                item['plan']['final_responder_id'] = 'codex'
                item['plan']['tasks'] = item['plan']['tasks'][:1]
            else:
                item.pop('plan')
            return 'completed', envelope(item)
        service = self.service(invoke)
        self.clarify(service)
        service.drive('job')
        self.assertEqual(roles, ['owner', 'collaborator', 'owner'])
        self.assertEqual(service.store.snapshot('job')['phase'], 'completed')
        self.assertEqual(service.material('job')[1][0]['task_id'], 'research')

    def test_invalid_placeholder_source_never_invokes(self):
        service = self.service(lambda *a, **kw: self.fail('unverified source must not run'))
        self.clarify(service)
        with service.store.transaction() as db:
            db.execute("UPDATE collab_calls SET text='unverifiable' WHERE call_id='entry'")
        service.drive('job')
        self.assertEqual(service.store.snapshot('job')['phase'], 'failed')

    def test_actual_execution_trace_disqualifies_placeholder(self):
        service = self.service(lambda *a, **kw: self.fail('must not run'))
        self.clarify(service)
        with service.store.transaction() as db:
            db.execute("INSERT INTO collab_calls(job_id,call_id,agent_id,role,state) VALUES ('job','prior','codex','collaborator','completed')")
        service.drive('job')
        self.assertEqual(service.store.snapshot('job')['phase'], 'failed')

    def test_quota_delivery_precedes_cancelled_placeholder(self):
        calls = []
        def invoke(job, agent, role, prompt, **kwargs):
            calls.append(role)
            item = answer()
            item.pop('plan')
            return 'completed', envelope(item)
        service = self.service(invoke)
        self.clarify(service)
        with service.store.transaction() as db:
            plan = json.loads(db.execute("SELECT plan FROM collab_jobs").fetchone()[0])
            plan['final_responder_id'] = 'deepseek-harness'
            db.execute('UPDATE collab_jobs SET plan=?', (json.dumps(plan),))
            db.execute('UPDATE collab_lifecycle SET used=7,round_limit=8')
        service.drive('job')
        self.assertEqual(calls, ['final'])
        self.assertEqual(service.store.snapshot('job')['phase'], 'completed')
        self.assertEqual(service.material('job')[1][0]['state'], 'cancelled')

    def test_stop_during_replan_is_not_overwritten(self):
        def invoke(*args, **kwargs):
            service.store.operate('job', 'chat', 'stop', 'stop')
            return 'completed', envelope(answer())
        service = self.service(invoke)
        self.clarify(service)
        service.drive('job')
        self.assertEqual(service.store.snapshot('job')['phase'], 'stopped')
        self.assertEqual(service.material('job')[1][0]['state'], 'cancelled')

    def test_new_material_prevents_stale_completion(self):
        calls = []
        def invoke(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                service.store.operate('job', 'chat', 'extra', 'append', 'New scope')
            return 'completed', envelope(answer())
        service = self.service(invoke)
        self.clarify(service)
        service.drive('job')
        self.assertEqual(len(calls), 2)
        self.assertEqual(service.store.snapshot('job')['phase'], 'completed')

    def test_replan_atomic_rollback(self):
        service = self.service(lambda *a, **kw: ('completed', envelope(answer())))
        self.clarify(service)
        with service.store.transaction() as db:
            db.execute("CREATE TRIGGER reject_finish BEFORE UPDATE OF phase ON collab_lifecycle WHEN NEW.phase='completed' BEGIN SELECT RAISE(ABORT,'injected failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            service.drive('job')
        self.assertEqual(service.material('job')[1][0]['state'], 'pending')
        self.assertEqual(service.material('job')[2][-1]['state'], 'reserved')
        self.assertEqual(service.store.snapshot('job')['phase'], 'working')

    def setup_web(self):
        self.patcher = patch.object(web, 'DB_PATH', self.path)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        identity = patch.object(web, 'SERVICE_INSTANCE_ID', 'isolated-test-instance')
        identity.start()
        self.addCleanup(identity.stop)
        self.path.touch()
        web.initialize_workbench_tables()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO workbench_messages(id,conversation_id,sender_id,recipient_id,content,created_at,status) VALUES (1,'chat','codex','user','','','pending')")
        web.register_active_run(1, 'chat', 'codex')
        self.addCleanup(lambda: web.unregister_active_run(1))

    def web_row(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            return db.execute('SELECT content,status FROM workbench_messages WHERE id=1').fetchone()

    def test_web_direct_answer_full_path_and_duplicate(self):
        self.direct_answer_path('codex', 'run_codex', web.update_codex_message)

    def test_web_deepseek_direct_answer_full_path_and_duplicate(self):
        self.direct_answer_path('deepseek-harness', 'run_deepseek_harness', web.update_workbench_message)

    def direct_answer_path(self, agent, worker_name, update):
        self.setup_web()
        calls = []
        def worker(*args):
            calls.append(1)
            update(1, envelope(answer()), 'completed')
            self.assertEqual(self.web_row()[1], 'pending')
        mention = '@Codex' if agent == 'codex' else '@DeepSeek Harness'
        with patch.object(web, worker_name, scripted_worker(worker)), patch.object(web, 'collaboration_service', side_effect=AssertionError('no execution service')):
            web.run_agent_task(agent, 'chat', mention + ' Q?', 1, 0)
            web.run_agent_task(agent, 'chat', mention + ' Q?', 1, 0)
        self.assertEqual(calls, [1])
        self.assertEqual(self.web_row()[1], 'completed')
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('SELECT count(*) FROM collaboration_entry_results').fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM collab_jobs").fetchone()[0], 0)

    def test_production_schema_rejects_running_and_survives_reinitialize(self):
        self.setup_web()
        with closing(sqlite3.connect(self.path)) as db, db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE workbench_messages SET status='running' WHERE id=1")
            db.execute("UPDATE workbench_messages SET status='completed',content='history' WHERE id=1")
        web.initialize_workbench_tables()
        self.assertEqual(self.web_row(), ('history', 'completed'))

    def test_both_workers_failure_or_missing_reply_cannot_complete(self):
        self.setup_web()
        for agent, name, update, mention in (
                ('codex', 'run_codex', web.update_codex_message, '@Codex'),
                ('deepseek-harness', 'run_deepseek_harness', web.update_workbench_message, '@DeepSeek Harness')):
            for outcome in ('failed', 'cancelled', 'no_reply'):
                with self.subTest(agent=agent, outcome=outcome):
                    with closing(sqlite3.connect(self.path)) as db, db:
                        # Even parseable old text must not count as transport success.
                        db.execute("UPDATE workbench_messages SET content=?,status='pending'", (envelope(answer()),))
                    web.register_active_run(1, 'chat', agent)
                    def worker(*args):
                        if outcome != 'no_reply':
                            update(1, envelope(answer()), outcome)
                    with patch.object(web, name, scripted_worker(worker)), patch.object(web, 'collaboration_service', side_effect=AssertionError('no execution')):
                        web.run_agent_task(agent, 'chat', mention + ' Q?', 1, 0)
                    self.assertEqual(self.web_row()[1], 'cancelled' if outcome == 'cancelled' else 'failed')
                    with closing(sqlite3.connect(self.path)) as db:
                        self.assertEqual(db.execute('SELECT count(*) FROM collaboration_entry_results').fetchone()[0], 0)

    def test_web_cancelled_result_stays_cancelled(self):
        self.setup_web()
        def worker(*args):
            web.stop_active_run(1)
            web.update_workbench_message(1, envelope(answer()), 'completed')
        with patch.object(web, 'run_codex', scripted_worker(worker)):
            web.run_agent_task('codex', 'chat', '@Codex Q?', 1, 0)
        self.assertEqual(self.web_row()[1], 'cancelled')

    def test_web_cancel_between_parse_and_commit(self):
        self.setup_web()
        real_parse = web.parse_entry_response
        def parse_then_stop(raw):
            result = real_parse(raw)
            web.stop_active_run(1)
            return result
        def worker(*args):
            web.update_codex_message(1, envelope(answer()), 'completed')
        with patch.object(web, 'run_codex', scripted_worker(worker)), patch.object(web, 'parse_entry_response', parse_then_stop):
            web.run_agent_task('codex', 'chat', '@Codex Q?', 1, 0)
        self.assertEqual(self.web_row()[1], 'cancelled')
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM collaboration_entry_results').fetchone()[0], 0)

    def test_web_execution_plan_still_registers(self):
        self.setup_web()
        service = self.service(lambda *a, **kw: self.fail('kick is captured; no CLI'))
        item = payload()
        item['status'] = 'continue_work'
        def worker(*args):
            web.update_codex_message(1, envelope(item), 'completed')
        with patch.object(web, 'run_codex', scripted_worker(worker)), patch.object(web, 'collaboration_service', return_value=service), patch.object(service, 'kick') as kick:
            web.run_agent_task('codex', 'chat', '@Codex Work', 1, 0)
        kick.assert_called_once_with('entry-1')
        self.assertEqual(service.store.snapshot('entry-1')['phase'], 'working')
        self.assertEqual(len(service.material('entry-1')[1]), 2)

    def test_web_protocol_error_preserves_reply(self):
        self.setup_web()
        def worker(*args):
            web.update_codex_message(1, envelope(payload()), 'completed')
        with patch.object(web, 'run_codex', scripted_worker(worker)):
            web.run_agent_task('codex', 'chat', '@Codex Q?', 1, 0)
        content, state = self.web_row()
        self.assertEqual(state, 'failed')
        self.assertIn('protocol incompatible', content)
        self.assertIn(envelope(payload()), content)

    def test_web_persistence_failure_rolls_back_success(self):
        self.setup_web()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON collaboration_entry_results BEGIN SELECT RAISE(ABORT,'injected'); END")
        def worker(*args):
            web.update_codex_message(1, envelope(answer()), 'completed')
        with patch.object(web, 'run_codex', scripted_worker(worker)):
            web.run_agent_task('codex', 'chat', '@Codex Q?', 1, 0)
        self.assertEqual(self.web_row()[1], 'failed')
        self.assertIn('persistence failed', self.web_row()[0])
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('SELECT count(*) FROM collaboration_entry_results').fetchone()[0], 0)

    def test_web_registration_failure_is_visible_and_not_retried(self):
        self.setup_web()
        item = payload()
        item['status'] = 'continue_work'
        def worker(*args):
            web.update_codex_message(1, envelope(item), 'completed')
        with patch.object(web, 'run_codex', scripted_worker(worker)), patch.object(web, 'collaboration_service', side_effect=RuntimeError('register unavailable')):
            web.run_agent_task('codex', 'chat', '@Codex Work', 1, 0)
        self.assertEqual(self.web_row()[1], 'failed')
        self.assertIn('registration/dispatch failed', self.web_row()[0])


if __name__ == '__main__':
    unittest.main()
