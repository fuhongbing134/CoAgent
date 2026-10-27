"""Task-5 isolated tests; synthetic identities/results are not backend acceptance."""
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from database_schema import ensure_schema
from execution_tracking import (ExecutionTracker, SessionIdentity, SessionReviewRequired,
                                SessionValidation, StaleRun, DomainBusy, invalidate_sessions)
from execution_runtime import Execution
from managed_process import ProcessResult
from session_configuration import ResolvedSessionConfiguration
import web_chat as web


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'test.db'
        ensure_schema(self.path)
        self.tracker = ExecutionTracker(self.path)
        self.identity = SessionIdentity('codex', 'synthetic:root', 'config', str(self.root), 0)

    def sql(self, statement, args=()):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(statement, args)]

    def session(self):
        return self.sql('SELECT * FROM execution_sessions')[0]

    def begin(self, room='room', instance='instance'):
        return self.tracker.begin_run(agent_id='codex', conversation_id=room, instance_id=instance,
            source_kind='collaboration', source_job_id='job', source_call_id='call',
            source_message_id=123, config_snapshot={})

    def finish(self, token, claim, sid='id', outcome='completed', evidence='confirmed', **kw):
        return self.tracker.finish_run(token, outcome=outcome, exit_evidence=evidence,
            session_claim=claim, session_validation=SessionValidation('verified', sid, **({'normal_completion': True} | kw)))

    def ready(self):
        token = self.begin()
        claim = self.tracker.claim_session(token, self.identity)
        self.tracker.mark_running(token)
        self.finish(token, claim)
        return token, claim

    def test_new_resume_single_authority_and_restart(self):
        self.ready()
        self.assertEqual((self.session()['state'], self.session()['generation']), ('ready', 1))
        token = self.begin(instance='new-instance')
        claim = self.tracker.claim_session(token, self.identity)
        self.assertEqual((claim.expected_id, claim.generation), ('id', 2))
        self.assertEqual(self.session()['state'], 'needs_validation')
        self.tracker.mark_running(token)
        self.finish(token, claim)
        self.assertEqual(self.sql('SELECT * FROM workbench_sessions'), [])
        self.assertEqual(self.sql('SELECT source_call_id FROM execution_runs')[0]['source_call_id'], 'call')

    def test_cancel_timeout_unknown_and_abnormal_do_not_write_ready(self):
        for scenario in ('cancel','timeout','unknown','abnormal'):
            with self.subTest(scenario=scenario):
                token = self.begin(scenario)
                claim = self.tracker.claim_session(token, self.identity)
                self.tracker.mark_running(token)
                if scenario == 'cancel':
                    self.tracker.request_cancel(token)
                self.finish(token, claim, sid=scenario, outcome='failed',
                            evidence='unknown' if scenario == 'unknown' else 'confirmed',
                            normal_completion=scenario == 'cancel')
                row = self.sql('SELECT * FROM execution_sessions WHERE conversation_id=?',(scenario,))[0]
                self.assertEqual(row['state'], 'needs_validation')
                self.assertIn(scenario, row['legacy_candidates_json'])
        self.assertEqual(self.sql("SELECT state FROM execution_domains WHERE conversation_id='unknown'")[0]['state'], 'quiescing')

    def test_claim_crash_and_directory_change_preserve_binding(self):
        self.ready()
        token = self.begin()
        self.tracker.claim_session(token, self.identity)
        self.assertEqual(self.session()['state'], 'needs_validation')
        with self.assertRaises(DomainBusy):
            with self.tracker._transaction() as db:
                invalidate_sessions(db, 'room')
        with self.assertRaises(DomainBusy):
            self.begin(instance='restart')

    def test_unique_scope_conflict_rolls_back_run_session_and_release(self):
        self.ready()
        token = self.begin('other')
        claim = self.tracker.claim_session(token, self.identity)
        self.tracker.mark_running(token)
        with self.assertRaises(sqlite3.IntegrityError):
            self.finish(token, claim)
        self.assertEqual(self.sql("SELECT state FROM execution_runs WHERE run_id=?",(token.run_id,))[0]['state'], 'running')
        self.assertEqual(self.sql("SELECT state FROM execution_domains WHERE conversation_id='other'")[0]['state'], 'running')
        self.assertEqual(self.sql("SELECT state FROM execution_sessions WHERE conversation_id='other'")[0]['state'], 'needs_validation')
        third = self.begin('separate')
        other = self.tracker.claim_session(third, replace(self.identity, backend_scope='another:root'))
        self.tracker.mark_running(third)
        self.finish(third, other)

    def test_old_generation_fence_and_instance_rejected(self):
        for component in ('generation','fence','instance'):
            with self.subTest(component=component):
                token = self.begin(component)
                claim = self.tracker.claim_session(token, self.identity)
                self.tracker.mark_running(token)
                bad_token = replace(token, **({'fence': token.fence + 1} if component=='fence'
                                              else {'instance_id':'old'} if component=='instance' else {}))
                bad_claim = replace(claim, generation=claim.generation-1) if component=='generation' else claim
                with self.assertRaises(StaleRun):
                    self.finish(bad_token, bad_claim, sid=component)

    def test_protocol_failure_can_keep_verified_session(self):
        token = self.begin()
        claim = self.tracker.claim_session(token, self.identity)
        self.tracker.mark_running(token)
        self.finish(token, claim, outcome='failed')
        self.assertEqual(self.session()['state'], 'ready')
        self.assertEqual(self.sql('SELECT state FROM execution_runs')[0]['state'], 'failed')

    def test_missing_or_mismatch_does_not_replace_binding(self):
        self.ready()
        token = self.begin()
        claim = self.tracker.claim_session(token, self.identity)
        self.tracker.mark_running(token)
        self.finish(token, claim, sid='wrong')
        self.assertEqual(self.session()['session_id'], 'id')
        self.assertEqual(self.session()['state'], 'needs_validation')

    def test_v2_later_legacy_records_merge_without_selecting_newest(self):
        self.sql("INSERT INTO execution_sessions(agent_id,conversation_id,legacy_candidates_json) VALUES ('codex','room',?)",
                 (json.dumps([dict(session_id='old',source_table='migration')]),))
        self.sql("INSERT INTO workbench_sessions VALUES ('room','new','9999')")
        token = self.begin()
        with self.assertRaises(SessionReviewRequired):
            self.tracker.claim_session(token, self.identity)
        row = self.session()
        self.assertEqual({c['session_id'] for c in json.loads(row['legacy_candidates_json'])}, {'old','new'})
        self.assertIsNone(row['session_id'])
        self.assertEqual(row['generation'], 0)

    def test_candidate_adoption_requires_selection_identity_and_tool_capability(self):
        candidate = dict(session_id='old', conversation_id='room', source_table='review',
                         backend_scope='wrong-scope')
        self.sql("INSERT INTO execution_sessions(agent_id,conversation_id,legacy_candidates_json) VALUES ('codex','room',?)",
                 (json.dumps([candidate]),))
        token = self.begin()
        adoption = dict(candidate=candidate, identity=self.identity, confirmed=True)
        for options in ({}, dict(adoption=adoption), dict(adoption=adoption, tools_disabled=True)):
            with self.assertRaises(SessionReviewRequired):
                self.tracker.claim_session(token, self.identity, **options)
        candidate['backend_scope'] = self.identity.backend_scope
        self.sql('UPDATE execution_sessions SET legacy_candidates_json=?',(json.dumps([candidate]),))
        claim = self.tracker.claim_session(token, self.identity, adoption=adoption, tools_disabled=True)
        self.assertTrue(claim.validation_only)
        self.assertEqual(claim.expected_id, 'old')

    def test_atomic_release_fault_rolls_back_session_ready(self):
        token = self.begin()
        claim = self.tracker.claim_session(token, self.identity)
        self.tracker.mark_running(token)
        self.sql("CREATE TRIGGER fail_release BEFORE UPDATE OF state ON execution_domains WHEN NEW.state='idle' BEGIN SELECT RAISE(ABORT,'fault'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.finish(token, claim)
        self.assertEqual(self.session()['state'], 'needs_validation')
        self.assertEqual(self.sql('SELECT state FROM execution_runs')[0]['state'], 'running')

    def test_config_change_invalidates_without_guessing_new(self):
        self.ready()
        token = self.begin()
        with self.assertRaises(SessionReviewRequired):
            self.tracker.claim_session(token, replace(self.identity, config_fingerprint='changed'))
        self.assertEqual((self.session()['state'], self.session()['session_id']), ('needs_validation','id'))

    def test_result_parser_all_adapters_and_ambiguous_ids(self):
        execution = Execution(self.path, instance_id='i', agent_id='codex', conversation_id='room',
                              working_directory=self.root, source_kind='workbench', message_id=1)
        execution.claim_session(self.identity)
        for adapter, key, typ in [('codex','thread_id','thread.started'), ('dsh','sessionId','session')]:
            def output(ids):
                return '\n'.join(json.dumps(dict(type=typ, **{key: value})) for value in ids)
            for ids, expected in [([], 'unverified'), (['a','a'],'verified'), (['a','b'],'invalid')]:
                result = execution.observe_session(adapter, output(ids), normal_completion=True)
                self.assertEqual(result.status, expected)

    def test_configuration_scope_and_secret_whitelist(self):
        kwargs = dict(adapter_id='dsh', storage_root=self.root, workspace=self.root,
                      executable=sys.executable, effective_config={'model':'m'}, evidence_reference='isolated-test')
        a = ResolvedSessionConfiguration.verified(**kwargs, profile='one')
        b = ResolvedSessionConfiguration.verified(**kwargs, profile='two')
        self.assertEqual(a.identity.backend_scope, b.identity.backend_scope)
        self.assertNotEqual(a.identity.config_fingerprint, b.identity.config_fingerprint)
        c = ResolvedSessionConfiguration.verified(**kwargs, profile='one', profile_isolates_storage=True)
        self.assertNotEqual(a.identity.backend_scope, c.identity.backend_scope)
        with self.assertRaises(ValueError):
            ResolvedSessionConfiguration.verified(**(kwargs | {'effective_config':{'api_key':'secret'}}))

    def test_candidate_validation_cannot_launch_business_command(self):
        candidate = dict(session_id='old', source_table='review', conversation_id='room')
        self.sql("INSERT INTO execution_sessions(agent_id,conversation_id,legacy_candidates_json) VALUES ('codex','room',?)",
                 (json.dumps([candidate]),))
        execution = Execution(self.path, instance_id='i', agent_id='codex', conversation_id='room',
                              working_directory=self.root, source_kind='workbench', message_id=1)
        execution.claim_session(self.identity, adoption=dict(candidate=candidate, identity=self.identity, confirmed=True),
                                tools_disabled=True)
        with patch('execution_runtime.ManagedProcess') as process:
            with self.assertRaisesRegex(RuntimeError, 'Restricted'):
                execution.launch([sys.executable, '-c', 'raise Exception()'], snapshot={})
        process.assert_not_called()
        self.assertTrue(execution.finish('failed'))
        self.assertEqual(self.session()['state'], 'needs_validation')
        self.assertEqual(self.session()['session_id'], 'old')

    def test_cancelled_outcome_without_intent_does_not_mark_ready(self):
        token = self.begin()
        claim = self.tracker.claim_session(token, self.identity)
        self.tracker.mark_running(token)
        self.finish(token, claim, outcome='cancelled')
        self.assertEqual(self.session()['state'], 'needs_validation')

    def test_patch_mutation_detected_without_persisting_credentials(self):
        patch_file = self.root / 'patch.yml'
        patch_file.write_text('secret: first', encoding='utf-8')
        config = ResolvedSessionConfiguration.verified(adapter_id='dsh', storage_root=self.root,
            workspace=self.root, executable=sys.executable, effective_config={}, evidence_reference='test',
            profile='headless', patch_path=patch_file)
        command = [sys.executable, '--profile', 'headless', '--patch', str(patch_file)]
        config.check_launch(command, self.root, {})
        patch_file.write_text('secret: changed', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'content changed'):
            config.check_launch(command, self.root, {})
        self.assertNotIn('secret', repr(config))


class SessionWiringTests(unittest.TestCase):
    # Reuse only fixture helpers, not the 4B test methods.
    from test_execution_wiring import WiringTests as _Fixture
    setUp = _Fixture.setUp
    clear_registry = _Fixture.clear_registry
    query = _Fixture.query
    message = _Fixture.message

    def configuration(self, agent, directory, executable, environment):
        return ResolvedSessionConfiguration.verified(
            adapter_id='codex' if agent == 'codex' else 'dsh', storage_root=self.root,
            workspace=directory, executable=executable, effective_config={'model':'fixture'},
            evidence_reference='ISOLATED FIXTURE ONLY',
            profile=web.DSH_PROFILE if agent != 'codex' else None,
            patch_path=web.DSH_AUTH_PATCH if agent != 'codex' else None)

    def test_enabled_workers_new_resume_never_call_old_accessors(self):
        commands = []
        def native_run(owner, command, **kwargs):
            kwargs['on_running'](owner)
            commands.append(command)
            if '--output-last-message' in command:
                Path(command[command.index('--output-last-message')+1]).write_text('answer', encoding='utf-8')
                output = b'{"type":"thread.started","thread_id":"fixture-codex"}\n'
            else:
                output = b'{"type":"session","sessionId":"fixture-dsh"}\n{"type":"final","text":"answer"}\n'
            return ProcessResult('completed', 0, output, b'', 'confirmed')
        patch_file = self.root / 'patch.yml'
        patch_file.write_text('fixture: true', encoding='utf-8')
        with patch.object(web, 'SESSION_CONFIGURATION_RESOLVER', self.configuration), \
             patch.object(web, 'resolve_codex_command', return_value=sys.executable), \
             patch.object(web, 'resolve_dsh_command', return_value=sys.executable), \
             patch.object(web, 'DSH_AUTH_PATCH', patch_file), \
             patch.object(web, 'get_codex_thread_id', side_effect=AssertionError('legacy read')), \
             patch.object(web, 'get_agent_session_id', side_effect=AssertionError('legacy read')), \
             patch.object(web, 'save_codex_thread_id', side_effect=AssertionError('legacy write')), \
             patch.object(web, 'save_agent_session_id', side_effect=AssertionError('legacy write')), \
             patch('execution_runtime.ManagedProcess.run', native_run):
            for number, agent in enumerate(('codex','codex','deepseek-harness','deepseek-harness'), 1):
                self.message(number, agent)
                web.run_agent_task(agent, 'chat', 'hello', number, 0)
        self.assertEqual([r['state'] for r in self.query('SELECT * FROM execution_sessions')], ['ready','ready'])
        self.assertEqual([r['generation'] for r in self.query('SELECT * FROM execution_sessions')], [2,2])
        self.assertEqual(len(self.query('SELECT * FROM execution_runs')), 4)
        self.assertIn('resume', commands[1])
        self.assertIn('--session-id', commands[3])
        self.assertEqual(self.query('SELECT * FROM workbench_sessions'), [])
        self.assertEqual(self.query('SELECT * FROM workbench_agent_sessions'), [])

    def test_enabled_directory_update_never_deletes_legacy(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO workbench_sessions VALUES ('chat','retained','')")
            db.execute("CREATE TRIGGER deny_legacy_delete BEFORE DELETE ON workbench_sessions BEGIN SELECT RAISE(ABORT,'legacy delete'); END")
            db.execute("INSERT INTO execution_sessions(agent_id,conversation_id,session_id) VALUES ('codex','chat','retained')")
        with patch.object(web, 'SESSION_CONFIGURATION_RESOLVER', self.configuration):
            web.save_working_directory('chat', str(self.root))
        self.assertEqual(self.query('SELECT * FROM workbench_sessions')[0]['codex_thread_id'], 'retained')
        self.assertEqual(self.query('SELECT * FROM execution_sessions')[0]['generation'], 1)

    def test_unverified_resolver_fails_closed_no_cli_no_fallback(self):
        self.message()
        with patch.object(web, 'SESSION_CONFIGURATION_RESOLVER', return_value=None), \
             patch.object(web, 'resolve_codex_command', return_value=sys.executable), \
             patch('execution_runtime.ManagedProcess.run') as launch:
            web.run_agent_task('codex', 'chat', 'hello', 1, 0)
        launch.assert_not_called()
        self.assertEqual(self.query('SELECT status FROM workbench_messages')[0]['status'], 'failed')

    def test_enabled_roles_and_replan_share_session_path(self):
        from collaboration_service import CollaborationService
        from test_collaboration_foundation import payload
        from test_direct_answer import envelope, answer
        service = CollaborationService(self.path, web.invoke_collaboration, web.cancel_collaboration)
        item = payload()
        item['status'] = 'continue_work'
        service.register_entry('job','chat','codex','work',item,'Plan')
        def native_run(owner, command, **kwargs):
            kwargs['on_running'](owner)
            Path(command[command.index('--output-last-message')+1]).write_text(envelope(answer()), encoding='utf-8')
            return ProcessResult('completed',0,b'{"type":"thread.started","thread_id":"role-session"}\n',b'','confirmed')
        with patch.object(web, 'SESSION_CONFIGURATION_RESOLVER', self.configuration), \
             patch.object(web, 'resolve_codex_command', return_value=sys.executable), \
             patch.object(web, 'collaboration_service', return_value=service), \
             patch('execution_runtime.ManagedProcess.run', native_run):
            for number, role in enumerate(('collaborator','owner','final','entry')):
                with service.store.transaction() as db:
                    db.execute("INSERT INTO collab_calls(job_id,call_id,agent_id,role,state) VALUES ('job',?,'codex',?,'reserved')",
                               (str(number),role))
                reply = web.invoke_collaboration('job','codex',role,'work',call_id=str(number),entry_mode=role=='entry')
                self.assertTrue(reply.dispatch_allowed)
        self.assertEqual(self.query('SELECT generation FROM execution_sessions')[0]['generation'], 4)
        self.assertEqual([r['source_call_id'] for r in self.query('SELECT * FROM execution_runs')], ['0','1','2','3'])

    @unittest.skipUnless(sys.platform == 'win32', 'Windows Job integration')
    def test_native_process_session_parsing_and_atomic_finish(self):
        execution = Execution(self.path, instance_id='fixture', agent_id='codex', conversation_id='native',
                              working_directory=self.root, source_kind='workbench', message_id=99)
        identity = SessionIdentity('codex','fixture:native','config',str(self.root),0)
        execution.claim_session(identity)
        result = execution.launch([sys.executable,'-c',
            'print(\'{"type":"thread.started","thread_id":"native-id"}\')'], snapshot={}, timeout=10)
        self.assertEqual(result.exit_evidence, 'confirmed')
        execution.observe_session('codex', result.stdout.decode(), normal_completion=result.returncode==0)
        self.assertTrue(execution.finish('completed'))
        row = self.query("SELECT * FROM execution_sessions WHERE conversation_id='native'")[0]
        self.assertEqual((row['state'],row['session_id']), ('ready','native-id'))


if __name__ == '__main__':
    unittest.main()
