"""Deterministic scheduler regressions; scripted transport is not a backend test."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from collaboration_service import CollaborationService
from collaboration_protocol import validate_payload
from test_collaboration_foundation import payload


class Issue8Tests(unittest.TestCase):
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
        def invoke(job, agent, role, prompt):
            roles.append(role)
            return 'completed', 'Result\n```agent-collaboration\n' + json.dumps(next(replies)) + '\n```'
        with tempfile.TemporaryDirectory() as root:
            service = CollaborationService(Path(root)/'test.db', invoke, lambda job: None)
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
        def invoke(*args):
            calls.append(args)
            return 'completed', 'No change\n```agent-collaboration\n' + json.dumps(item) + '\n```'
        with tempfile.TemporaryDirectory() as root:
            service = CollaborationService(Path(root)/'test.db', invoke, lambda job: None)
            service.register_entry('job', 'chat', 'codex', 'Request', item, 'Plan')
            service.drive('job')
            self.assertEqual(len(calls), 1)
            self.assertEqual(service.store.snapshot('job')['phase'], 'blocked')

    def test_invalid_amendment_rolls_back_and_reaccept_is_inert(self):
        item = payload()
        item['status'] = 'continue_work'
        with tempfile.TemporaryDirectory() as root:
            service = CollaborationService(Path(root)/'test.db', lambda *a: None, lambda job: None)
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
