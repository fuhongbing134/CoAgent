"""Controlled receipts test classification, not real MXC enforcement."""

from pathlib import Path
import sys
import unittest

from tests.verify import run_mxc_unit as runner


PYTHON = sys.executable
CODEX = str(Path(sys.executable).with_name('fixture-codex.exe'))


def complete_output():
    return '\n'.join([
        f'MXC_UNIT_INNER={PYTHON}',
        'MXC_UNIT_DISCOVERED=4',
        'MXC_UNIT_LOOPBACK_OK=1',
        'MXC_UNIT_SKIP=explicit live CLI opt-in required',
        'MXC_UNIT_RAN=4 SKIPPED=1',
    ])


def process_records():
    return [
        dict(pid=100, parent_pid=50, executable=CODEX,
             created='2026-11-23T15:00:00+00:00', helper=False),
        dict(pid=110, parent_pid=100, executable=CODEX,
             created='2026-11-23T15:00:01+00:00', helper=True),
        dict(pid=120, parent_pid=110, executable=PYTHON,
             created='2026-11-23T15:00:02+00:00', helper=False),
        dict(pid=130, parent_pid=120, executable=PYTHON,
             created='2026-11-23T15:00:03+00:00', helper=False),
    ]


class ResultStageTests(unittest.TestCase):
    def test_complete_receipts_accept_current_counts_and_skip_reason(self):
        self.assertEqual(runner.result_stage('mxc', complete_output(), 0, PYTHON), 'passed')
        no_skips = complete_output().replace('MXC_UNIT_DISCOVERED=4', 'MXC_UNIT_DISCOVERED=5')
        no_skips = no_skips.replace('MXC_UNIT_SKIP=explicit live CLI opt-in required\n', '')
        no_skips = no_skips.replace('MXC_UNIT_RAN=4 SKIPPED=1', 'MXC_UNIT_RAN=5 SKIPPED=0')
        self.assertEqual(runner.result_stage('mxc', no_skips, 0, PYTHON), 'passed')

    def test_suite_success_does_not_supply_missing_backend_evidence(self):
        for backend in ('unknown', 'elevated'):
            with self.subTest(backend=backend):
                self.assertEqual(
                    runner.result_stage(backend, complete_output(), 0, PYTHON),
                    'mxc_start_unverified',
                )

    def test_missing_or_mismatched_interpreter_is_not_started(self):
        wrong_python = str(Path(PYTHON).with_name('different-python.exe'))
        for output in (
            complete_output().replace(f'MXC_UNIT_INNER={PYTHON}\n', ''),
            complete_output().replace(f'MXC_UNIT_INNER={PYTHON}', f'MXC_UNIT_INNER={wrong_python}'),
        ):
            with self.subTest(output=output):
                self.assertEqual(runner.result_stage('mxc', output, 0, PYTHON), 'inner_not_started')

    def test_zero_exit_cannot_hide_missing_completion_receipts(self):
        required = (
            'MXC_UNIT_DISCOVERED=4',
            'MXC_UNIT_LOOPBACK_OK=1',
            'MXC_UNIT_SKIP=explicit live CLI opt-in required',
            'MXC_UNIT_RAN=4 SKIPPED=1',
        )
        for line in required:
            with self.subTest(missing=line):
                output = complete_output().replace(line, '')
                self.assertEqual(runner.result_stage('mxc', output, 0, PYTHON), 'test_failed')
        self.assertEqual(
            runner.result_stage('mxc', f'MXC_UNIT_INNER={PYTHON}\n', 0, PYTHON),
            'test_failed',
        )

    def test_inconsistent_counts_do_not_pass(self):
        changes = (
            ('MXC_UNIT_DISCOVERED=4', 'MXC_UNIT_DISCOVERED=5'),
            ('MXC_UNIT_RAN=4 SKIPPED=1', 'MXC_UNIT_RAN=4 SKIPPED=2'),
            ('MXC_UNIT_RAN=4 SKIPPED=1', 'MXC_UNIT_RAN=4 SKIPPED=5'),
            ('MXC_UNIT_DISCOVERED=4', 'MXC_UNIT_DISCOVERED=invalid'),
        )
        for old, new in changes:
            with self.subTest(change=new):
                output = complete_output().replace(old, new)
                self.assertEqual(runner.result_stage('mxc', output, 0, PYTHON), 'test_failed')

    def test_empty_discovery_does_not_prove_suite_coverage(self):
        output = complete_output().replace('MXC_UNIT_DISCOVERED=4', 'MXC_UNIT_DISCOVERED=0')
        output = output.replace('MXC_UNIT_SKIP=explicit live CLI opt-in required\n', '')
        output = output.replace('MXC_UNIT_RAN=4 SKIPPED=1', 'MXC_UNIT_RAN=0 SKIPPED=0')
        self.assertEqual(runner.result_stage('mxc', output, 0, PYTHON), 'test_failed')

    def test_loopback_failure_has_its_own_stage(self):
        output = complete_output().replace('MXC_UNIT_LOOPBACK_OK=1', 'MXC_UNIT_LOOPBACK_FAILED=denied')
        self.assertEqual(runner.result_stage('mxc', output, 1, PYTHON), 'loopback_unavailable')

    def test_nonzero_cli_exit_is_not_overridden_by_complete_output(self):
        self.assertEqual(runner.result_stage('mxc', complete_output(), 7, PYTHON), 'test_failed')


class BackendSelectionTests(unittest.TestCase):
    def test_current_stderr_selection_is_used(self):
        stderr = 'Windows sandbox selection: mxc\n'
        for helper in (None, 110):
            with self.subTest(helper=helper):
                self.assertEqual(runner.select_backend(stderr, helper), ('mxc', 'cli_stderr'))

    def test_verified_helper_supplies_run_local_evidence(self):
        self.assertEqual(runner.select_backend('', 110), ('mxc', 'process_ancestry'))

    def test_requested_backend_and_unrelated_text_are_not_observations(self):
        for stderr in ('', 'requested_backend=mxc\n', 'Windows sandbox selection: mxc maybe\n'):
            with self.subTest(stderr=stderr):
                self.assertEqual(runner.select_backend(stderr, None), ('unknown', 'unknown'))

    def test_conflicting_runtime_evidence_remains_unknown(self):
        for stderr in (
            'Windows sandbox selection: elevated\n',
            'Windows sandbox selection: unelevated\n',
            'Windows sandbox selection: mxc\nWindows sandbox selection: elevated\n',
        ):
            with self.subTest(stderr=stderr):
                self.assertEqual(runner.select_backend(stderr, 110), ('unknown', 'unknown'))


class HelperAncestryTests(unittest.TestCase):
    def test_helper_is_on_the_inner_process_chain_to_the_exact_cli(self):
        self.assertEqual(runner.mxc_helper_pid(process_records(), 130, 100, CODEX), 110)
        records = process_records()
        for process in records:
            process['created'] = '2026-11-23T15:00:00+00:00'
        self.assertEqual(runner.mxc_helper_pid(records, 130, 100, CODEX), 110)

    def test_helper_on_a_different_branch_is_not_evidence(self):
        records = process_records()
        records[1]['helper'] = False
        records.append(dict(pid=200, parent_pid=100, executable=CODEX,
                            created='2026-11-23T15:00:01+00:00', helper=True))
        self.assertIsNone(runner.mxc_helper_pid(records, 130, 100, CODEX))

    def test_wrong_cli_root_or_helper_image_is_not_evidence(self):
        self.assertIsNone(runner.mxc_helper_pid(process_records(), 130, 999, CODEX))
        for executable in (None, str(Path(CODEX).with_name('other-codex.exe'))):
            with self.subTest(executable=executable):
                records = process_records()
                records[1]['executable'] = executable
                self.assertIsNone(runner.mxc_helper_pid(records, 130, 100, CODEX))

    def test_missing_or_cyclic_ancestry_cannot_be_accepted(self):
        self.assertIsNone(runner.mxc_helper_pid(process_records()[:-1], 130, 100, CODEX))
        for parent in (999, 130):
            with self.subTest(helper_parent=parent):
                records = process_records()
                records[1]['parent_pid'] = parent
                self.assertIsNone(runner.mxc_helper_pid(records, 130, 100, CODEX))

    def test_parent_created_after_child_invalidates_process_identity(self):
        for index in (0, 1, 2):
            with self.subTest(parent_index=index):
                records = process_records()
                records[index]['created'] = '2026-11-23T15:00:04+00:00'
                self.assertIsNone(runner.mxc_helper_pid(records, 130, 100, CODEX))


if __name__ == '__main__':
    unittest.main()
