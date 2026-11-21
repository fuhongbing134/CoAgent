"""Version-only Job/stdio smoke probe; NOT the full 4B acceptance test.

Run: E:\\Python\\python.exe -B verify_real_backends.py
No database calls, prompts, sessions, patches, or production configuration writes.
ManagedProcess creates temporary standard-stream files and OS process/Job handles.
The external executables' own --version implementation remains outside our control.
"""
import json
import os
from pathlib import Path
import shutil
import sys

# Also suppress imported modules' bytecode writes when invoked without -B.
sys.dont_write_bytecode = True

from workbench.execution.managed_process import ManagedProcess
import web_chat


def emit(value):
    # ASCII JSON remains printable in older PowerShell/code-page environments.
    print(json.dumps(value, ensure_ascii=True, sort_keys=True), flush=True)


def environment_error(error):
    if isinstance(error, PermissionError) or getattr(error, 'winerror', None) == 5:
        return 'environment_permission_denied'
    if isinstance(error, FileNotFoundError):
        return 'environment_executable_missing'
    return 'environment_or_launch_error'


def probe(backend):
    report = dict(backend=backend, scope='version_only', status='not_accepted',
                  resolved=None, command=None, outcome=None, returncode=None,
                  exit_evidence=None, error_type=None, winerror=None,
                  stdout_preview='', stderr_preview='')
    try:
        resolver = {'codex': web_chat.resolve_codex_command, 'deepseek-harness': web_chat.resolve_dsh_command, 'grok': web_chat.resolve_grok_command}[backend]
        resolved = resolver()
        report['resolved'] = resolved
        if not resolved:
            report.update(error_type='ExecutableNotResolved',
                          note='Environment lookup failed; missing path or access restriction, not necessarily an installation failure.')
            return report
        env = os.environ.copy()
        if backend in {'codex', 'grok'}:
            command = [resolved, '--version']
        else:
            command, use_shell, electron = web_chat.build_dsh_command(resolved, ['--version'])
            if use_shell:
                raise ValueError('Shell launch is unsupported by this probe')
            if electron:
                env['ELECTRON_RUN_AS_NODE'] = '1'
        # ManagedProcess requires an absolute executable, including COMSPEC.
        if not Path(command[0]).is_absolute():
            executable = shutil.which(command[0])
            if not executable:
                raise FileNotFoundError('Launch executable could not be resolved')
            command[0] = executable
        command[0] = str(Path(command[0]).resolve(strict=True))
        report['command'] = command
        # Print resolution before starting, so a stalled launch is identifiable.
        emit(dict(stage='resolved', backend=backend, resolved=resolved, command=command))
        result = ManagedProcess().run(
            command, cwd=str(Path(__file__).resolve().parents[2]), env=env,
            timeout=15, settle_timeout=5, cleanup_timeout=5, capture_limit=65536)
        report.update(outcome=result.outcome, returncode=result.returncode,
                      exit_evidence=result.exit_evidence, error_type=result.error_type,
                      winerror=result.winerror,
                      stdout_preview=result.stdout[:512].decode('utf-8', errors='replace'),
                      stderr_preview=result.stderr[:512].decode('utf-8', errors='replace'))
        passed = (result.outcome == 'completed' and result.returncode == 0
                  and result.exit_evidence == 'confirmed' and result.error_type is None
                  and bool(result.stdout.strip() or result.stderr.strip()))
        report['status'] = 'version_probe_passed' if passed else 'not_accepted'
        report['note'] = ('Only this --version launch and Job-local exit were checked. '
                          'new/resume, cancellation, descendants and external delegation are NOT accepted.')
        if result.winerror == 5:
            report['environment_issue'] = 'environment_permission_denied'
    except Exception as error:
        report.update(error_type=type(error).__name__, winerror=getattr(error, 'winerror', None),
                      environment_issue=environment_error(error),
                      note='Resolution/launch failed; no backend acceptance. Permission denial does not identify its ACL/sandbox cause.')
    return report


def main():
    emit(dict(scope='version_only', full_backend_acceptance='not_tested',
              note='No Agent sessions or database operations. Temporary stdio files/processes are created; --version behavior belongs to each CLI.'))
    reports = []
    for backend in ('codex', 'deepseek-harness', 'grok'):
        report = probe(backend)
        reports.append(report)
        emit(report)
    passed = all(r['status'] == 'version_probe_passed' for r in reports)
    emit(dict(version_probes_passed=passed, full_backend_acceptance='not_tested',
              four_b_released=False))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
