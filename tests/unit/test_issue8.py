"""Deterministic scheduler regressions; scripted transport is not a backend test."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from workbench.collaboration.collaboration_service import CollaborationService
from workbench.collaboration.collaboration_protocol import validate_payload
from workbench.collaboration.collaboration_entry import parse_entry_response
from tests.unit.test_collaboration_foundation import payload


class Issue8Tests(unittest.TestCase):
    @staticmethod
    def reply(plan=None, status='completed', invite=False):
        item = payload()
        item.pop('plan')
        item['status'] = status
        if plan is not None:
            item['plan'] = copy.deepcopy(plan)
        text = 'Result\n```agent-collaboration\n' + json.dumps(item) + '\n```'
        return text + ('\n@DeepSeek Harness' if invite else '')

    def initial(self, final='codex'):
        item = payload()
        item['status'] = 'continue_work'
        item['plan']['final_responder_id'] = final
        item['plan']['tasks'] = item['plan']['tasks'][:1]
        return item

    def test_completion_empty_or_omitted_plan_preserves_history(self):
        for final in ('codex', 'deepseek-harness'):
            for empty in (False, True):
                with self.subTest(final=final, empty=empty), tempfile.TemporaryDirectory() as root:
                    item = self.initial(final)
                    end = copy.deepcopy(item['plan'])
                    end['tasks'] = []
                    calls = []
                    def invoke(job, agent, role, prompt, **kwargs):
                        calls.append(role)
                        return 'completed', self.reply(end if role == 'owner' and empty else None)
                    service = CollaborationService(Path(root)/'test.db', invoke, lambda job: True)
                    service.register_entry('job', 'chat', 'codex', 'Request', item, 'Plan')
                    service.drive('job')
                    self.assertEqual(service.store.snapshot('job')['phase'], 'completed')
                    self.assertEqual(calls, ['collaborator', 'owner'] + ([] if final == 'codex' else ['final']))
                    view, tasks, _ = service.material('job')
                    self.assertEqual(view['plan'], item['plan'])
                    self.assertEqual(len(tasks), 1)
                    self.assertEqual(tasks[0]['state'], 'completed')
                    self.assertTrue(tasks[0]['result'])

    def test_completion_guards_identity_pending_and_active_calls(self):
        for case in ('identity', 'pending', 'active'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as root:
                item = self.initial()
                service = CollaborationService(Path(root)/'test.db', lambda *a, **kw: None, lambda job: True)
                service.register_entry('job', 'chat', 'codex', 'Request', item, 'Plan')
                service.store.reserve('job', [('owner', 'codex', 'owner')])
                end = copy.deepcopy(item['plan'])
                end['tasks'] = []
                if case == 'identity':
                    end['final_responder_id'] = 'deepseek-harness'
                if case == 'active':
                    with service.store.transaction() as db:
                        db.execute("UPDATE collab_tasks SET state='completed' WHERE job_id='job'")
                        db.execute("INSERT INTO collab_calls(job_id,call_id,agent_id,role) VALUES ('job','active','codex','collaborator')")
                text = self.reply(end)
                parsed = parse_entry_response(text, role='owner')[1]
                accepted = service.store.accept('job', 'owner', text, parsed)
                life = service.store.snapshot('job')
                self.assertEqual(accepted, case != 'identity')
                self.assertEqual(life['phase'], 'failed' if case == 'identity' else 'blocked')
                self.assertIn('cannot change roles' if case == 'identity' else 'unfinished planned tasks', life['reason'])
                self.assertEqual(len(service.material('job')[1]), 1)

    def test_incremental_self_or_other_dispatch_preserves_old_result(self):
        for other in (False, True):
            with self.subTest(other=other), tempfile.TemporaryDirectory() as root:
                item = self.initial()
                delta = copy.deepcopy(item['plan'])
                delta['tasks'] = [dict(task_id='next', agent_id='deepseek-harness' if other else 'codex',
                                       instruction='Next', depends_on=['research'])]
                calls, owners, old_results = [], [], []
                def invoke(job, agent, role, prompt, **kwargs):
                    calls.append((agent, role))
                    if role == 'owner':
                        owners.append(role)
                        old_results.append(service.material('job')[1][0]['result'])
                        if len(owners) == 1:
                            return 'completed', self.reply(delta, 'request_collaboration' if other else 'continue_work', other)
                    return 'completed', self.reply()
                service = CollaborationService(Path(root)/'test.db', invoke, lambda job: True)
                service.register_entry('job', 'chat', 'codex', 'Request', item, 'Plan')
                service.drive('job')
                self.assertEqual(service.store.snapshot('job')['phase'], 'completed')
                self.assertEqual(calls, [('codex','collaborator'), ('codex','owner'),
                                         ('deepseek-harness' if other else 'codex','collaborator'), ('codex','owner')])
                tasks = {t['task_id']: t for t in service.material('job')[1]}
                self.assertTrue(all(t['state'] == 'completed' for t in tasks.values()))
                self.assertEqual(tasks['research']['result'], old_results[0])
                self.assertEqual(old_results[0], old_results[1])
                self.assertEqual(service.material('job')[0]['plan']['tasks'][0], item['plan']['tasks'][0])

    def test_illegal_incremental_plans_leave_plan_and_tasks_unchanged(self):
        for case in ('tamper', 'duplicate', 'unknown', 'self', 'cycle'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as root:
                item = self.initial()
                service = CollaborationService(Path(root)/'test.db', lambda *a, **kw: None, lambda job: True)
                service.register_entry('job', 'chat', 'codex', 'Request', item, 'Plan')
                with service.store.transaction() as db:
                    db.execute("UPDATE collab_tasks SET state='completed',result='old-result' WHERE job_id='job'")
                before = copy.deepcopy(service.material('job')[:2])
                delta = copy.deepcopy(item['plan'])
                new = dict(task_id='next', agent_id='codex', instruction='Next', depends_on=['research'])
                delta['tasks'] = [new]
                if case == 'tamper':
                    changed = copy.deepcopy(item['plan']['tasks'][0])
                    changed['instruction'] = 'Changed'
                    delta['tasks'].append(changed)
                elif case == 'duplicate':
                    delta['tasks'].append(copy.deepcopy(new))
                elif case == 'unknown':
                    new['depends_on'] = ['missing']
                elif case == 'self':
                    new['depends_on'] = ['next']
                else:
                    new['depends_on'] = ['second']
                    delta['tasks'].append(dict(task_id='second', agent_id='codex', instruction='Second', depends_on=['next']))
                service.store.reserve('job', [('owner','codex','owner')])
                text = self.reply(delta, 'continue_work')
                if case == 'duplicate':
                    with self.assertRaisesRegex(ValueError, 'duplicate'):
                        parse_entry_response(text, role='owner')
                    candidate = json.loads(text.split('```agent-collaboration\n')[1].split('\n```')[0])
                else:
                    candidate = parse_entry_response(text, role='owner')[1]
                self.assertFalse(service.store.accept('job', 'owner', text, candidate))
                after = service.material('job')
                self.assertEqual(after[0]['plan'], before[0]['plan'])
                self.assertEqual(after[1], before[1])
                self.assertEqual(service.store.snapshot('job')['phase'], 'failed')

    def test_append_self_task_executes_once(self):
        item = payload()
        item['status'] = 'continue_work'
        item['plan']['final_responder_id'] = 'codex'
        item['plan']['tasks'] = item['plan']['tasks'][:1]
        amended = copy.deepcopy(item)
        amended['plan']['tasks'].append(dict(task_id='record', agent_id='codex',
                                           instruction='Record', depends_on=['research']))
        completed = payload()
        completed.pop('plan')
        replies = iter([completed, amended, completed, completed])
        roles = []
        def invoke(job, agent, role, prompt, **kwargs):
            roles.append(role)
            return 'completed', 'Result\n```agent-collaboration\n' + json.dumps(next(replies)) + '\n```'
        with tempfile.TemporaryDirectory() as root:
            service = CollaborationService(Path(root)/'test.db', invoke, lambda job: True)
            service.register_entry('job', 'chat', 'codex', 'Request', item, 'Plan')
            service.drive('job')
            self.assertEqual(service.store.snapshot('job')['phase'], 'completed')
            self.assertEqual(roles, ['collaborator', 'owner', 'collaborator', 'owner'])
            self.assertTrue(all(t['state'] == 'completed' for t in service.material('job')[1]))

    def test_no_progress_blocks_after_one_decision(self):
        item = payload()
        item['status'] = 'continue_work'
        item['plan']['tasks'] = [dict(task_id='external', agent_id='deepseek-harness', instruction='Review', depends_on=[])]
        calls = []
        def invoke(*args, **kwargs):
            calls.append(args)
            return 'completed', 'No change\n```agent-collaboration\n' + json.dumps(item) + '\n```'
        with tempfile.TemporaryDirectory() as root:
            service = CollaborationService(Path(root)/'test.db', invoke, lambda job: True)
            service.register_entry('job', 'chat', 'codex', 'Request', item, 'Plan')
            service.drive('job')
            self.assertEqual(len(calls), 1)
            self.assertEqual(service.store.snapshot('job')['phase'], 'blocked')

    def test_invalid_amendment_rolls_back_and_reaccept_is_inert(self):
        item = payload()
        item['status'] = 'continue_work'
        with tempfile.TemporaryDirectory() as root:
            service = CollaborationService(Path(root)/'test.db', lambda *a, **kw: None, lambda job: True)
            service.register_entry('job', 'chat', 'codex', 'Request', item, 'Plan')
            service.store.reserve('job', [('call', 'codex', 'owner')])
            bad = copy.deepcopy(item)
            bad['plan']['tasks'].append(dict(task_id='new', agent_id='codex', instruction='New', depends_on=[]))
            bad['plan']['tasks'][0]['instruction'] = 'Changed'
            self.assertFalse(service.store.accept('job', 'call', 'Invalid', bad))
            self.assertEqual(len(service.material('job')[1]), 2)
            self.assertFalse(service.store.accept('job', 'call', 'Invalid', bad))

    def test_role_restrictions(self):
        item = payload()
        item.pop('plan')
        item['status'] = 'continue_work'
        for role in ('collaborator', 'final'):
            with self.assertRaises(ValueError):
                validate_payload(item, role=role)


if __name__ == '__main__':
    unittest.main()
