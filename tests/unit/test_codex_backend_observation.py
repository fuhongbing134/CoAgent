"""Controlled backend evidence and SQLite wiring, not real MXC acceptance."""

from contextlib import ExitStack
import io
import json
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

import web_chat as web
import tests.unit.test_execution_wiring as wiring
from workbench.adapters import codex_diagnostics as diagnostics
from workbench.execution.managed_process import ProcessResult
from workbench.execution.web_runs import AgentRunCancelled


def process_evidence(helper=110):
    """Already verified, same-invocation fixture; these are not real process IDs."""
    return {
        'cli_pid': 100,
        'helper_pid': helper,
        'tool_pid': 120,
        'cli_created_filetime': '134000000000000000',
        'helper_created_filetime': '134000000000000010',
        'tool_created_filetime': '134000000000000020',
    }


class BackendSelectionTests(unittest.TestCase):
    def test_known_stderr_backend_is_observed_without_process_evidence(self):
        for backend in ('mxc', 'elevated', 'unelevated'):
            with self.subTest(backend=backend):
                self.assertEqual(
                    diagnostics.select_backend(f'Windows sandbox selection: {backend}\n'),
                    (backend, 'cli_stderr'),
                )

    def test_configuration_requests_do_not_prove_a_backend(self):
        for stderr in ('', 'windows.sandbox = "mxc"', 'sandbox: workspace-write',
                       'requested_backend=mxc', 'Windows sandbox availability: mxc'):
            with self.subTest(stderr=stderr):
                self.assertEqual(diagnostics.select_backend(stderr), ('unknown', 'unknown'))

    def test_verified_process_evidence_supplies_missing_stderr_and_agrees_with_mxc(self):
        self.assertEqual(
            diagnostics.select_backend('', process_evidence()),
            ('mxc', 'process_ancestry'),
        )

        self.assertEqual(
            diagnostics.select_backend('Windows sandbox selection: mxc\n', process_evidence()),
            ('mxc', 'cli_stderr'),
        )

    def test_conflicting_or_unknown_stderr_is_not_filtered_away(self):
        for stderr in (
            'Windows sandbox selection: mxc\nWindows sandbox selection: elevated\n',
            'Windows sandbox selection: unelevated\nWindows sandbox selection: mxc\n',
            'Windows sandbox selection: future-backend\nWindows sandbox selection: mxc\n',
            'Windows sandbox selection: \n',
            'Windows sandbox selection: future-backend\n',
            'Windows sandbox selection: mxc extra\n',
        ):
            for evidence in (None, process_evidence()):
                with self.subTest(stderr=stderr, evidence=evidence):
                    self.assertEqual(
                        diagnostics.select_backend(stderr, evidence), ('unknown', 'unknown'),
                    )

        for backend in ('elevated', 'unelevated'):
            with self.subTest(backend=backend):
                self.assertEqual(
                    diagnostics.select_backend(
                        f'Windows sandbox selection: {backend}\n', process_evidence(),
                    ),
                    ('unknown', 'unknown'),
                )


class CodexObservationWiringTests(unittest.TestCase):
    # Reuse the isolated database helpers, without inheriting unrelated test cases.
    setUp = wiring.WiringTests.setUp
    clear_registry = wiring.WiringTests.clear_registry
    query = wiring.WiringTests.query
    message = wiring.WiringTests.message

    def launch(self, number=1, *, evidence=None, stderr=b'', outcome='completed',
               code=0, mode='new', permission='default', profile=None, write_error=None):
        self.message(number)
        with patch.object(web, 'resolve_working_directory', return_value=self.root):
            execution = web.begin_execution(number, source_kind='workbench')
        observer = MagicMock()
        observer.__enter__.return_value = observer
        observer.collect.return_value = evidence
        events = []
        observer.collect.side_effect = lambda: (events.append('collect'), evidence)[1]
        observer.__exit__.side_effect = lambda *args: events.append('close')
        command = [sys.executable, 'exec']
        if mode == 'resume':
            command += ['resume', 'fixture-session']
        if profile is not None:
            command += ['-c', f'default_permissions="{profile}"']
        else:
            command += ['--sandbox', 'read-only' if permission == 'read-only' else 'workspace-write']

        def native_run(owner, actual_command, **kwargs):
            self.assertEqual(actual_command, command)
            kwargs['on_running'](owner)
            kwargs['on_stage']('after_resume', owner)
            events.append('process_return')
            return ProcessResult(outcome, code, b'fixture-output', stderr, 'confirmed')

        patches = [
            patch.object(diagnostics, 'cli_version', return_value='0.167.0-alpha.3.2'),
            patch.object(diagnostics, 'MxcProcessObservation', return_value=observer),
            patch('workbench.execution.execution_runtime.ManagedProcess.run', native_run),
        ]
        if write_error is not None:
            patches.append(patch.object(execution.tracker, 'record_codex_backend', side_effect=write_error))
        previous_stage = MagicMock()
        with ExitStack() as stack:
            stack.enter_context(patches[0])
            observation_class = stack.enter_context(patches[1])
            for pending in patches[2:]:
                stack.enter_context(pending)
            result = None
            error = None
            try:
                result = web.run_managed_process(
                    number, command, cwd=self.root, on_stage=previous_stage,
                    diagnostic_metadata={
                        'session_mode': mode,
                        'permission_mode': permission,
                        'permission_profile_requested': profile,
                    },
                )
            except Exception as caught:
                error = caught
            finally:
                web.finish_execution(number, outcome='failed' if outcome == 'timeout' else outcome)
            observation_class.assert_called_once_with(sys.executable)
            previous_stage.assert_called_once()
            self.assertEqual(previous_stage.call_args.args[0], 'after_resume')
        row = self.query('SELECT * FROM execution_runs WHERE run_id=?', (execution.token.run_id,))[0]
        return result, error, json.loads(row['config_snapshot']), observer, events, execution

    def test_new_resume_and_read_only_keep_launch_policy_and_record_same_run(self):
        for number, (mode, permission, profile) in enumerate(
            (('new', 'default', 'step1-git'), ('resume', 'default', 'step1-git'),
             ('new', 'read-only', None), ('new', 'default', None)), 1,
        ):
            evidence = process_evidence(110 + number)
            with self.subTest(mode=mode, permission=permission, profile=profile):
                result, error, snapshot, observer, events, _ = self.launch(
                    number, evidence=evidence, mode=mode, permission=permission, profile=profile,
                )
                self.assertIsNone(error)
                self.assertEqual(result.stdout, 'fixture-output')
                self.assertEqual(snapshot['observed_backend'], 'mxc')
                self.assertEqual(snapshot['backend_evidence_source'], 'process_ancestry')
                self.assertEqual(snapshot['backend_process_evidence'], evidence)
                self.assertEqual(snapshot['cli_version'], '0.167.0-alpha.3.2')
                self.assertEqual(snapshot['executable'], sys.executable)
                self.assertEqual(snapshot['launch_mode'], mode)
                self.assertEqual(snapshot['permission_mode'], permission)
                self.assertEqual(snapshot['permission_profile'], profile)
                if profile is None:
                    self.assertEqual(snapshot['sandbox'], 'read-only' if permission == 'read-only' else 'workspace-write')
                else:
                    self.assertNotIn('sandbox', snapshot)
                    self.assertEqual(snapshot['permission_profile_requested'], profile)
                observer.on_stage.assert_called_once()
                self.assertEqual(observer.on_stage.call_args.args[0], 'after_resume')
                self.assertEqual(events, ['process_return', 'collect', 'close'])

    def test_conflicting_run_evidence_does_not_publish_a_positive_backend(self):
        _, error, snapshot, _, _, _ = self.launch(
            evidence=process_evidence(), stderr=b'Windows sandbox selection: elevated\n',
        )
        self.assertIsNone(error)
        self.assertEqual(snapshot['observed_backend'], 'unknown')
        self.assertEqual(snapshot['backend_evidence_source'], 'unknown')

    def test_process_evidence_does_not_leak_to_another_agent_or_subsequent_run(self):
        self.message(9, 'grok')
        other = web.begin_execution(9, source_kind='workbench')
        _, error, first, _, _, _ = self.launch(evidence=process_evidence())
        self.assertIsNone(error)
        result, error, second, _, _, execution = self.launch(2)
        self.assertIsNone(error)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(execution.released)
        other_row = self.query('SELECT * FROM execution_runs WHERE run_id=?', (other.token.run_id,))[0]
        other_snapshot = json.loads(other_row['config_snapshot'])
        self.assertEqual(first['backend_process_evidence'], process_evidence())
        self.assertEqual(second['observed_backend'], 'unknown')
        self.assertEqual(second['backend_evidence_source'], 'unknown')
        self.assertIsNone(second.get('backend_process_evidence'))
        self.assertNotIn('backend_process_evidence', other_snapshot)
        self.assertNotIn('observed_backend', other_snapshot)
        web.finish_execution(9, outcome='failed')

    def test_stop_and_timeout_keep_observed_evidence_and_lifecycle_error(self):
        for number, (outcome, error_type) in enumerate(
            (('cancelled', AgentRunCancelled), ('timeout', subprocess.TimeoutExpired)), 1,
        ):
            with self.subTest(outcome=outcome):
                _, error, snapshot, _, events, execution = self.launch(
                    number, evidence=process_evidence(), outcome=outcome, code=None,
                )
                self.assertIsInstance(error, error_type)
                self.assertEqual(snapshot['observed_backend'], 'mxc')
                self.assertEqual(snapshot['backend_process_evidence'], process_evidence())
                self.assertEqual(events[-1], 'close')
                self.assertTrue(execution.released)

    def test_diagnostic_database_failure_does_not_turn_success_into_failure(self):
        result, error, snapshot, _, events, execution = self.launch(
            evidence=process_evidence(), write_error=sqlite3.OperationalError('fixture unavailable'),
        )
        self.assertIsNone(error)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(snapshot['observed_backend'], 'unknown')
        self.assertEqual(events[-1], 'close')
        self.assertTrue(execution.released)


class ProcessObservationTests(unittest.TestCase):
    def observation(self, payload):
        observer = diagnostics.MxcProcessObservation(sys.executable)
        observer.cli_pid = 100
        observer.created = process_evidence()['cli_created_filetime']
        child = MagicMock()
        child.wait.return_value = 0
        child.poll.return_value = 0
        child.stdout = io.BytesIO(payload)
        observer.observer = child
        return observer, child

    def test_matching_root_collects_only_compact_process_evidence(self):
        payload = dict(process_evidence(), executable=sys.executable, ignored='not persisted')
        observer, child = self.observation(json.dumps(payload).encode())
        with observer:
            self.assertEqual(observer.collect(), process_evidence())
        child.wait.assert_called_once_with(timeout=0.2)
        self.assertTrue(child.stdout.closed)
        child.kill.assert_not_called()

    def test_unrelated_root_or_invalid_output_is_unavailable(self):
        payload = dict(process_evidence(), executable=sys.executable)
        variants = [b'invalid json', b'[]', b'{}']
        for key, value in (
            ('cli_pid', 999), ('cli_created_filetime', '134000000000000001'),
            ('executable', str(sys.executable) + '.different'),
        ):
            variants.append(json.dumps(dict(payload, **{key: value})).encode())
        for data in variants:
            with self.subTest(data=data):
                observer, child = self.observation(data)
                with observer:
                    self.assertIsNone(observer.collect())
                self.assertTrue(child.stdout.closed)

    def test_unavailable_or_slow_observer_does_not_fail_main_execution(self):
        owner = MagicMock(pid=100)
        owner.creation_filetime.return_value = int(process_evidence()['cli_created_filetime'])
        with patch.object(diagnostics.os, 'name', 'nt'), \
             patch.object(diagnostics.subprocess, 'Popen', side_effect=OSError('fixture denied')):
            with diagnostics.MxcProcessObservation(sys.executable) as observer:
                observer.on_stage('after_resume', owner)
                self.assertIsNone(observer.collect())
        observer, child = self.observation(b'')
        child.wait.side_effect = [subprocess.TimeoutExpired('observer', 0.2), 0]
        child.poll.return_value = None
        with observer:
            self.assertIsNone(observer.collect())
        child.kill.assert_called_once()
        self.assertEqual(child.wait.call_args_list[-1].kwargs, {'timeout': 1})
        self.assertTrue(child.stdout.closed)


if __name__ == '__main__':
    unittest.main()
