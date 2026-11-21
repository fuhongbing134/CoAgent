"""Isolated Grok boundaries; these tests never start a model or use the real DB."""
from contextlib import closing
import json
import sqlite3
import sys
import unittest
from unittest.mock import patch

import web_chat as web
from workbench.adapters.grok_adapter import command, parse_output, requested_recipients
from workbench.execution.managed_process import ProcessResult
import tests.unit.test_execution_wiring as wiring
import tests.unit.test_database_schema as migration


def output(sid='session-1', reason='end_turn', text='answer'):
    return '\n'.join(json.dumps(e) for e in [dict(type='text', data=text),
        dict(type='end', sessionId=sid, stopReason=reason)])


class Boundaries(unittest.TestCase):
    def test_permissions_are_explicit(self):
        normal = command('grok', 'prompt', 'cwd', None, False)
        final = command('grok', 'prompt', 'cwd', 'id', True)
        self.assertNotIn('--always-approve', normal + final)
        self.assertEqual(normal[normal.index('--permission-mode')+1], 'auto')
        self.assertEqual(final[final.index('--permission-mode')+1], 'dontAsk')
        self.assertEqual(final[final.index('--deny')+1], '*')
        self.assertIn('--no-subagents', final)
        self.assertIn('--disable-web-search', final)

    def test_completion_and_ambiguous_ids(self):
        self.assertEqual(parse_output(output())[:3], ('answer', 'session-1', []))
        for data in [json.dumps(dict(type='text',data='partial')), output(reason='unknown'),
                     output(sid=''), output()+'\n'+output('other')]:
            with self.subTest(data=data):
                self.assertTrue(parse_output(data)[2])
        self.assertFalse(parse_output(output()+'\n'+output())[2])

    def test_documented_end_aliases_and_abnormal_reasons(self):
        usage = json.dumps(dict(type='usage', stopReason='tool_use'))+'\n'
        for reason in ('end_turn', 'EndTurn'):
            self.assertFalse(parse_output(usage+output(reason=reason))[2])
        for reason in ('max_tokens', 'max_turn_requests', 'refusal', 'cancelled', 'unknown', 'END_TURN', None, []):
            with self.subTest(reason=reason):
                self.assertTrue(parse_output(output(reason=reason))[2])

    def test_diagnostics_are_bounded_and_redacted(self):
        result = parse_output('warning token=secret https://host/key\n'+output())
        self.assertFalse(result[2])
        self.assertNotIn('secret', str(result[3]))
        self.assertNotIn('https://', str(result[3]))

    def test_client_cannot_redirect_a_target(self):
        all_ids = ['codex','deepseek-harness','grok']
        self.assertEqual(requested_recipients(all_ids, all_ids[:2], False), all_ids[:2])
        for request in [[], ['unknown'], ['grok','grok'], 'grok']:
            with self.assertRaises(ValueError):
                requested_recipients(all_ids, request, False)
        with self.assertRaises(ValueError):
            requested_recipients(['grok'], ['codex'], True)
        with self.assertRaises(ValueError):
            web.agent_worker('unknown')


class GrokWiring(unittest.TestCase):
    setUp = wiring.WiringTests.setUp
    clear_registry = wiring.WiringTests.clear_registry
    query = wiring.WiringTests.query
    message = wiring.WiringTests.message

    def run_worker(self, stdout, code=0, outcome='completed', final=False, stop=False, evidence='confirmed'):
        self.message(1, 'grok')
        web.begin_execution(1, source_kind='workbench')
        (self.root/'AGENTS.md').write_text('fixture', encoding='utf-8')
        (self.root/'specs').mkdir(exist_ok=True)
        (self.root/'specs'/'README.md').write_text('fixture',encoding='utf-8')
        def native(owner, args, **kwargs):
            kwargs['on_running'](owner)
            self.args = args
            if stop:
                web.stop_active_run(1)
            return ProcessResult(outcome, code, stdout.encode(), b'', evidence)
        previous = getattr(web.COLLAB_CONTEXT,'final_delivery',False)
        web.COLLAB_CONTEXT.final_delivery = final
        try:
            with patch.object(web,'resolve_grok_command',return_value=sys.executable), \
                 patch('workbench.execution.execution_runtime.ManagedProcess.run',native):
                reply = web.run_grok('chat','hello',1,0)
            web.finish_execution(1,outcome=reply.status)
            return reply
        finally:
            web.COLLAB_CONTEXT.final_delivery = previous

    def test_worker_success_and_session_saved(self):
        reply = self.run_worker(output(), final=True)
        self.assertEqual(reply.status,'completed')
        self.assertEqual(self.query('SELECT session_id FROM workbench_agent_sessions')[0]['session_id'],'session-1')
        self.assertEqual(self.query('SELECT state FROM execution_domains')[0]['state'],'idle')
        self.assertIn('--deny', self.args)

    def test_documented_alias_saves_legacy_binding_without_claiming_verification(self):
        reply = self.run_worker(output(reason='EndTurn'))
        self.assertEqual(reply.status, 'completed')
        self.assertEqual(self.query('SELECT session_id FROM workbench_agent_sessions')[0]['session_id'], 'session-1')
        self.assertEqual(reply.session_validation.status, 'unverified')

    def test_abnormal_end_never_saves_binding(self):
        self.assertEqual(self.run_worker(output(reason='max_tokens')).status, 'failed')
        self.assertEqual(self.query('SELECT * FROM workbench_agent_sessions'), [])

    def test_auto_refusal_diagnostic_is_visible_and_not_bound(self):
        refusal = json.dumps(dict(type='error', message='Auto mode blocked this action token=secret'))
        reply = self.run_worker(refusal)
        self.assertEqual(reply.status, 'failed')
        self.assertIn('Auto mode blocked', reply.text)
        self.assertNotIn('secret', reply.text)
        self.assertEqual(self.query('SELECT * FROM workbench_agent_sessions'), [])

    def test_failure_never_saves_binding(self):
        reply = self.run_worker(output()+'\n'+json.dumps(dict(type='error',message='failed')))
        self.assertEqual(reply.status,'failed')
        self.assertEqual(self.query('SELECT * FROM workbench_agent_sessions'),[])

    def test_resume_mismatch_preserves_old_binding(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO workbench_agent_sessions VALUES ('chat','grok-headless','old','stamp')")
        self.assertEqual(self.run_worker(output()).status,'failed')
        self.assertEqual(self.query('SELECT session_id FROM workbench_agent_sessions')[0]['session_id'],'old')

    def test_partial_output_is_failed(self):
        self.assertEqual(self.run_worker('{"type":"text","data":"partial"}').status,'failed')
        self.assertEqual(self.query('SELECT * FROM workbench_agent_sessions'),[])

    def test_cancelled_reply_cannot_save_or_overwrite(self):
        self.assertEqual(self.run_worker(output(),stop=True).status,'cancelled')
        self.assertEqual(self.query('SELECT * FROM workbench_agent_sessions'),[])
        self.assertEqual(self.query('SELECT status FROM workbench_messages')[0]['status'],'cancelled')

    def test_timeout_is_failed_and_releases_domain(self):
        self.assertEqual(self.run_worker('',outcome='timeout').status,'failed')
        self.assertEqual(self.query('SELECT state FROM execution_domains')[0]['state'],'idle')

    def test_unknown_exit_retains_domain(self):
        self.assertEqual(self.run_worker(output(),evidence='unknown').status,'failed')
        self.assertEqual(self.query('SELECT state FROM execution_domains')[0]['state'],'quiescing')
        self.assertEqual(self.query('SELECT * FROM workbench_agent_sessions'),[])

    def test_session_observer_rejects_non_normal_end(self):
        self.message(1,'grok')
        execution=web.begin_execution(1,source_kind='workbench')
        result=execution.observe_session('grok',output(reason='unknown'),normal_completion=True)
        self.assertFalse(result.normal_completion)
        web.finish_execution(1,outcome='failed')

    def test_bridge_register_and_send_with_grok_identity(self):
        import agent_bridge as bridge
        with patch.object(bridge.bridge,'DB_PATH',self.path):
            with patch.object(bridge.bridge,'AGENT_ID','codex'):
                bridge.register_agent('Codex','fixture','fixture','fixture')
            with patch.object(bridge.bridge,'AGENT_ID','grok'):
                self.assertEqual(bridge.register_agent('Grok','fixture','fixture','fixture')['agent_id'],'grok')
                bridge.send_message('codex','isolated hello','chat')
        row=self.query('SELECT sender_id,recipient_id,content FROM messages')[0]
        self.assertEqual(tuple(row.values()),('grok','codex','isolated hello'))

    def test_actual_grok_transport_roles_and_duplicate(self):
        from workbench.collaboration.collaboration_service import CollaborationService
        from tests.unit.test_collaboration_foundation import payload
        from tests.unit.test_direct_answer import envelope
        service = CollaborationService(self.path,web.invoke_collaboration,web.cancel_collaboration,
                                       finalize_message=web.finalize_message)
        item=payload()
        item['status']='continue_work'
        service.register_entry('job','chat','codex','work',item,'Plan')
        (self.root/'AGENTS.md').write_text('fixture',encoding='utf-8')
        (self.root/'specs').mkdir()
        (self.root/'specs'/'README.md').write_text('fixture',encoding='utf-8')
        reply_payload=payload()
        reply_payload.pop('plan')
        text=envelope(reply_payload)
        launches=[]
        def native(owner,args,**kwargs):
            kwargs['on_running'](owner)
            launches.append(args)
            return ProcessResult('completed',0,output(text=text).encode(),b'','confirmed')
        with patch.object(web,'collaboration_service',return_value=service), \
             patch.object(web,'resolve_grok_command',return_value=sys.executable), \
             patch.object(web,'resolve_working_directory',return_value=self.root), \
             patch('workbench.execution.execution_runtime.ManagedProcess.run',native):
            for role in ('collaborator','owner','final'):
                with service.store.transaction() as db:
                    db.execute("INSERT INTO collab_calls(job_id,call_id,agent_id,role,state) VALUES ('job',?,'grok',?,'reserved')",(role,role))
                reply=web.invoke_collaboration('job','grok',role,'prompt',call_id=role)
                self.assertTrue(reply.dispatch_allowed)
                duplicate=web.invoke_collaboration('job','grok',role,'prompt',call_id=role)
                self.assertEqual(duplicate.message_id,reply.message_id)
                if role=='collaborator':
                    receipt=service.store.accept_result('job',role,reply.text,reply_payload,message_id=reply.message_id)
                    self.assertEqual(receipt.disposition,'accepted')
        self.assertEqual(len(launches),3)
        self.assertNotIn('--deny',launches[0])
        self.assertIn('--deny',launches[2])


class GrokMigration(unittest.TestCase):
    setUp = migration.MigrationTests.setUp
    legacy = migration.MigrationTests.legacy

    def test_legacy_aliases_keep_candidates_in_one_domain(self):
        from workbench.storage.database_schema import ensure_schema
        with closing(self.legacy()) as db, db:
            db.executemany('INSERT INTO workbench_agent_sessions VALUES (?,?,?,?)',
                          [('g','grok-headless','s1','old'),('g','grok','s2','new')])
        ensure_schema(self.path, allow_legacy=True)
        with closing(sqlite3.connect(self.path)) as db:
            rows=db.execute("SELECT agent_id,state,legacy_candidates_json FROM execution_sessions WHERE conversation_id='g'").fetchall()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0][:2],('grok','needs_validation'))
        self.assertEqual({x['original_agent_key'] for x in json.loads(rows[0][2])},{'grok','grok-headless'})
