"""Focused checks for the Grok agent wiring; no real Grok CLI is started."""
import json
import os
import unittest
from pathlib import Path
from unittest import mock

import agent_registry
import web_chat
from collaboration_entry import route_message
from execution_inspection import canonical_adapter


class GrokAgentTests(unittest.TestCase):
    def test_registry_and_routing(self):
        ids = [a.agent_id for a in agent_registry.default_registry().enabled_agents()]
        self.assertEqual(ids, ['codex', 'deepseek-harness', 'grok'])
        self.assertEqual(route_message('@Grok hi'), (['grok'], True))
        self.assertEqual(route_message('@grok-build hi'), (['grok'], True))
        self.assertEqual(route_message('@Codex hi'), (['codex'], True))
        self.assertEqual(route_message('@deepseek hi'), (['deepseek-harness'], True))
        self.assertEqual(route_message('hi')[0], ['codex', 'deepseek-harness', 'grok'])
        self.assertEqual(web_chat.clean_message_prompt('@Grok 你好'), '你好')
        self.assertEqual(canonical_adapter('grok'), 'grok')
        self.assertEqual(canonical_adapter('grok-headless'), 'grok')
        self.assertIs(web_chat.agent_worker('grok'), web_chat.run_grok)
        self.assertIs(web_chat.agent_worker('codex'), web_chat.run_codex)
        self.assertIs(web_chat.agent_worker('deepseek-harness'), web_chat.run_deepseek_harness)

    def test_command_construction(self):
        p, cwd = Path('p.txt'), Path('w')
        new = web_chat.build_grok_command('grok', p, cwd, None, False)
        self.assertEqual(new, ['grok', '--prompt-file', 'p.txt', '--output-format', 'streaming-json',
                               '--cwd', 'w', '--permission-mode', 'auto', '--no-subagents'])
        self.assertNotIn('--always-approve', new)
        self.assertNotIn('--deny', new)
        final = web_chat.build_grok_command('grok', p, cwd, 'abc-1', True)
        self.assertIn('--resume', final)
        self.assertEqual(final[final.index('--resume') + 1], 'abc-1')
        self.assertNotIn('--always-approve', final)
        self.assertEqual(final[final.index('--permission-mode') + 1], 'dontAsk')
        self.assertEqual(final[final.index('--deny') + 1], '*')
        self.assertIn('--no-subagents', final)
        self.assertIn('--disable-web-search', final)
        self.assertNotIn('--session-id', final)

    def test_output_parsing(self):
        out = '\n'.join([json.dumps({'type': 'text', 'data': '你'}), json.dumps({'type': 'text', 'data': '好'}),
                         'permission required: write_file was not run, as a headless run cannot ask for approval',
                         json.dumps({'type': 'end', 'sessionId': 's-1', 'stopReason': 'end_turn'})])
        answer, sid, errors, notes = web_chat.parse_grok_output(out)
        self.assertEqual((answer, sid, errors), ('你好', 's-1', []))
        self.assertEqual(len(notes), 1)
        _, _, errors, _ = web_chat.parse_grok_output(json.dumps({'type': 'error', 'message': 'boom'}))
        self.assertEqual(errors, ['boom'])

    def test_resolve_command(self):
        with mock.patch.dict(os.environ, {'GROK_CLI': 'definitely-missing-grok-xyz'}):
            self.assertIsNone(web_chat.resolve_grok_command())


if __name__ == '__main__':
    unittest.main()
