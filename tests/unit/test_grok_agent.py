"""Focused checks for the Grok agent wiring; no real Grok CLI is started."""
import json
import os
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from workbench.agents import agent_registry
from workbench.adapters.web_backends import run_grok
import web_chat
from workbench.collaboration.collaboration_entry import route_message
from workbench.execution.execution_inspection import canonical_adapter


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
        options = dict(file_profile=Path('files.md'), allow_git=True)
        new = web_chat.build_grok_command('grok', p, cwd, None, False, **options)
        self.assertEqual(new, ['grok', '--prompt-file', 'p.txt', '--output-format', 'streaming-json',
                               '--cwd', 'w', '--permission-mode', 'dontAsk',
                               '--allow', 'Read', '--allow', 'Grep', '--allow', 'Bash(git *)',
                               '--agent', 'files.md', '--allow', 'MCPTool(workbench_files__*)',
                               '--no-subagents'])
        resumed = web_chat.build_grok_command('grok', p, cwd, 'abc-1', False, **options)
        self.assertEqual(resumed, new + ['--resume', 'abc-1'])
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

    def test_single_run_diagnostic_isolation_and_cleanup(self):
        for enabled, session, final, failure in (
            (False, None, False, False),
            (True, 'prior', False, False),
            (True, None, True, False),
            (True, None, False, False),
            (True, None, False, True),
        ):
            with self.subTest(enabled=enabled, session=session, final=final, failure=failure):
                execution = SimpleNamespace(working_directory=Path.cwd(), token=object(),
                                            tracker=mock.Mock(), result=None)
                active = {'execution': execution, 'grok_diagnostic': enabled,
                          'cancel_requested': False, 'state_lock': mock.MagicMock()}
                diagnostic = enabled and not session and not final
                paths = []

                def process(_message_id, command, **_options):
                    self.assertEqual('--debug' in command, diagnostic)
                    self.assertEqual('--debug-file' in command, diagnostic)
                    self.assertEqual('--agent' in command, not final)
                    self.assertEqual('--deny' in command, final)
                    if diagnostic:
                        path = Path(command[command.index('--debug-file') + 1])
                        server_path = path.with_name('workbench-files.events')
                        paths.extend((path, server_path))
                        path.write_text(
                            '{"event":"tools_registered","server":"workbench_files","status":"ok",'
                            '"tools":["workbench_files__delete_file","private-tool"],"prompt":"secret"}\n',
                            encoding='utf-8',
                        )
                        server_path.write_text('started\nprivate-content\ntools_list_response\n', encoding='ascii')
                    if failure:
                        raise subprocess.TimeoutExpired(command, 1)
                    return subprocess.CompletedProcess(command, 0, '', '')

                reply = run_grok(
                    'conversation', 'prompt', 17, 16, AGENT_GROK_TIMEOUT_SECONDS=1,
                    COLLAB_CONTEXT=SimpleNamespace(final_delivery=final), GROK_SESSION_KEY='grok',
                    active_run=lambda _: active, build_agent_prompt=lambda *_: 'example',
                    build_grok_command=web_chat.build_grok_command, is_run_cancelled=lambda _: False,
                    parse_grok_output=lambda _: ('answer', session, [], []),
                    prepare_execution_session=lambda *_: session, resolve_grok_command=lambda: 'fake-grok',
                    run_managed_process=process, save_agent_session_id=mock.Mock(),
                    touch_agent_last_seen=mock.Mock(), worker_reply=lambda *args: args,
                )
                self.assertEqual(reply[2], 'failed' if failure else 'completed')
                self.assertEqual(execution.tracker.record_grok_diagnostic.call_count, int(diagnostic))
                if diagnostic:
                    token, summary = execution.tracker.record_grok_diagnostic.call_args.args
                    self.assertIs(token, execution.token)
                    self.assertEqual(summary['raw_cleanup'], 'removed')
                    self.assertEqual(summary['client_tools'], ['workbench_files__delete_file'])
                    self.assertEqual(summary['server_events'], ['started', 'tools_list_response'])
                    self.assertNotIn('private', json.dumps(summary))
                    self.assertNotIn('secret', json.dumps(summary))
                    self.assertTrue(all(not path.exists() for path in paths))


if __name__ == '__main__':
    unittest.main()
