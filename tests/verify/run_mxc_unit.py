"""Run the unit suite through Codex MXC.

Invoke the installed base python.exe directly from ordinary PowerShell.
The unit suite itself uses the project's .venv by default.
The HTTP tests need loopback access. Use --permission-profile to select
an existing Codex profile that permits it.
"""

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
from workbench.adapters.codex_diagnostics import cli_version, observed_backend

RUN_UNIT_TESTS = """\
import os
import socket
import sys
import tempfile
import unittest

project, fixtures, scratch = sys.argv[1:]
print("MXC_UNIT_INNER=" + sys.executable, flush=True)
print(f"MXC_UNIT_INNER_PID={os.getpid()}", flush=True)
sys.path.insert(0, project)
os.environ.update(COAGENT_LINK_FIXTURES=fixtures, TEMP=scratch, TMP=scratch, TMPDIR=scratch)
tempfile.tempdir = scratch
suite = unittest.defaultTestLoader.discover(os.path.join(project, 'tests', 'unit'))
print(f"MXC_UNIT_DISCOVERED={suite.countTestCases()}", flush=True)
try:
    with socket.socket() as listener, socket.socket() as client:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        client.settimeout(2)
        client.connect(listener.getsockname())
        peer, _ = listener.accept()
        peer.close()
except OSError as error:
    loopback_ok = False
    print(f"MXC_UNIT_LOOPBACK_FAILED={error}", flush=True)
else:
    loopback_ok = True
    print("MXC_UNIT_LOOPBACK_OK=1", flush=True)
result = unittest.TextTestRunner(verbosity=1).run(suite)
for _, reason in result.skipped:
    print(f"MXC_UNIT_SKIP={reason}")
print(f"MXC_UNIT_RAN={result.testsRun} SKIPPED={len(result.skipped)}")
raise SystemExit(0 if loopback_ok and result.wasSuccessful() else 1)
"""

# Use the Windows host to inspect only processes on the child's parent chain.
# Match the full helper invocation, not a substring of a regular CLI argument.
PROCESS_CHAIN = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$processId = INNER_PID
$chain = @(
    for ($depth = 0; $depth -lt 12 -and $processId -gt 0; $depth++) {
        $process = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $processId"
        if (-not $process -or -not $process.CreationDate) { break }
        [pscustomobject]@{
            pid = $process.ProcessId
            parent_pid = $process.ParentProcessId
            executable = $process.ExecutablePath
            created = $process.CreationDate.ToUniversalTime().ToString('o')
            helper = $process.CommandLine -match '^\s*(?:"[^"]+"|[^"\s]+)\s+(?:"--__codex-windows-mxc"|--__codex-windows-mxc)\s*$'
        }
        if ($processId -eq CLI_PID) { break }
        $processId = $process.ParentProcessId
    }
)
ConvertTo-Json -InputObject $chain -Compress
"""


def mxc_helper_pid(processes, inner_pid, cli_pid, executable):
    """Accept a helper only on a live chain anchored to this CLI process."""
    records = {item['pid']: item for item in processes}
    visited = set()
    pid = inner_pid
    helper = None
    child_created = None
    expected = os.path.normcase(os.path.normpath(executable))
    while pid in records and pid not in visited:
        visited.add(pid)
        item = records[pid]
        if child_created is not None and item['created'] > child_created:
            return None  # Windows can reuse the recorded parent's PID.
        if pid == cli_pid:
            return helper
        image = item['executable']
        if pid != inner_pid and item['helper'] and image:
            if os.path.normcase(os.path.normpath(image)) == expected:
                helper = pid
        pid = item['parent_pid']
        child_created = item['created']
    return None


def inspect_mxc_helper(inner_pid, cli_pid, executable):
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / (
        r'WindowsPowerShell\v1.0\powershell.exe'
    )
    script = PROCESS_CHAIN.replace('INNER_PID', str(inner_pid)).replace('CLI_PID', str(cli_pid))
    try:
        result = subprocess.run(
            [str(powershell), '-NoProfile', '-NonInteractive', '-Command', script],
            capture_output=True, encoding='utf-8', check=True, timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        processes = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return mxc_helper_pid(processes, inner_pid, cli_pid, executable)


def run_suite(command, stderr_file):
    lines = []
    inner_pid = helper_pid = None
    # Keep stderr out of a pipe while the parent services the live stdout stream.
    with subprocess.Popen(
        command, cwd=PROJECT, stdout=subprocess.PIPE, stderr=stderr_file,
        encoding='utf-8', errors='replace',
    ) as process:
        for line in process.stdout:
            lines.append(line)
            print(line, end='', flush=True)
            marker = re.fullmatch(r'MXC_UNIT_INNER_PID=([1-9][0-9]*)', line.rstrip('\r\n'))
            if marker and inner_pid is None:
                inner_pid = int(marker[1])
                helper_pid = inspect_mxc_helper(inner_pid, process.pid, command[0])
        exit_code = process.wait()
        if helper_pid is not None:
            print(f'cli_pid={process.pid} mxc_helper_pid={helper_pid} inner_pid={inner_pid}')
    stderr_file.seek(0)
    stderr = stderr_file.read().decode('utf-8', errors='replace')
    return subprocess.CompletedProcess(command, exit_code, ''.join(lines), stderr), helper_pid


def select_backend(stderr, helper_pid):
    selections = re.findall(r'Windows sandbox selection: ([^\r\n]*)', stderr)
    if selections:
        # Reject conflicting values even when the shared parser doesn't know a value.
        if {selection.strip() for selection in selections} != {'mxc'}:
            return 'unknown', 'unknown'
        return observed_backend(stderr, requested='mxc')
    if helper_pid is not None:
        return 'mxc', 'process_ancestry'
    return 'unknown', 'unknown'


def result_stage(backend, stdout, cli_exit_code, expected_python):
    if backend != 'mxc':
        return 'mxc_start_unverified'
    lines = stdout.splitlines()
    inner = [line.removeprefix('MXC_UNIT_INNER=') for line in lines if line.startswith('MXC_UNIT_INNER=')]
    expected = os.path.normcase(os.path.normpath(expected_python))
    if len(inner) != 1 or os.path.normcase(os.path.normpath(inner[0])) != expected:
        return 'inner_not_started'
    if any(line.startswith('MXC_UNIT_LOOPBACK_FAILED=') for line in lines):
        return 'loopback_unavailable'
    if cli_exit_code != 0 or lines.count('MXC_UNIT_LOOPBACK_OK=1') != 1:
        return 'test_failed'
    discovered = re.findall(r'^MXC_UNIT_DISCOVERED=([0-9]+)$', stdout, re.MULTILINE)
    ran = re.findall(r'^MXC_UNIT_RAN=([0-9]+) SKIPPED=([0-9]+)$', stdout, re.MULTILINE)
    if len(discovered) != 1 or len(ran) != 1:
        return 'test_failed'
    executed, skipped = map(int, ran[0])
    reasons = sum(line.startswith('MXC_UNIT_SKIP=') and bool(line.removeprefix('MXC_UNIT_SKIP=').strip())
                  for line in lines)
    if executed != int(discovered[0]) or reasons != skipped or skipped >= executed:
        return 'test_failed'
    return 'passed'


def prepare_links(root):
    from _winapi import CreateJunction

    database_links = root / 'instance-hardlink'
    database_links.mkdir()
    original = database_links / 'isolated.db'
    original.touch()
    os.link(original, database_links / 'linked.db')

    workspace_links = root / 'workspace-hardlink'
    (workspace_links / 'sub').mkdir(parents=True)
    note = workspace_links / 'note.txt'
    note.write_text('original', encoding='utf-8')
    os.link(note, workspace_links / 'sub' / 'alias.txt')

    database_directory = root / 'instance-junction' / 'real'
    database_directory.mkdir(parents=True)
    (database_directory / 'isolated.db').touch()
    junction = database_directory.with_name('alias')
    CreateJunction(str(database_directory), str(junction))
    return junction


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', default='codex', help='Codex CLI command or executable path')
    parser.add_argument('--python', type=Path, default=PROJECT / '.venv' / 'Scripts' / 'python.exe')
    parser.add_argument('--permission-profile', default=':workspace')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('MXC verification requires Windows')
    executable = shutil.which(args.codex)
    python = args.python.resolve()
    print(f'outer={sys.executable} inner_requested={python} cli={executable or "unknown"} '
          f'permission_profile={args.permission_profile} requested_backend=mxc', flush=True)
    if executable is None:
        print('stage=cli_invalid')
        return 1
    executable = str(Path(executable).absolute())
    print(f'cli_version={cli_version(executable)}', flush=True)

    with ExitStack() as cleanup:
        try:
            root = Path(cleanup.enter_context(tempfile.TemporaryDirectory(prefix='.mxc-unit-', dir=PROJECT)))
            scratch = root / 'temp'
            scratch.mkdir()
            fixtures = root / 'links'
            fixtures.mkdir()
            runner = root / 'run_suite.py'
            runner.write_text(RUN_UNIT_TESTS, encoding='utf-8')
            stderr_file = cleanup.enter_context(tempfile.TemporaryFile(dir=scratch))
            junction = prepare_links(fixtures)
        except OSError as error:
            print(f'stage=fixture_prepare_failed error={error}')
            return 1
        cleanup.callback(junction.rmdir)
        command = [
            executable, '-c', 'windows.sandbox=mxc', 'sandbox',
            '--include-managed-config', '--permission-profile', args.permission_profile,
            '--cd', str(PROJECT), '--', str(python), '-B', '-u', '-X', 'utf8',
            str(runner), str(PROJECT), str(fixtures), str(scratch),
        ]
        try:
            result, helper_pid = run_suite(command, stderr_file)
        except OSError as error:
            print(f'stage=cli_invalid error={error}')
            return 1
        print(result.stderr, end='', file=sys.stderr)
        print(f'cli_exit_code={result.returncode}')
        backend, source = select_backend(result.stderr, helper_pid)
        stage = result_stage(backend, result.stdout, result.returncode, str(python))
        print(f'observed_backend={backend} backend_evidence_source={source} stage={stage}')
        return result.returncode or int(stage != 'passed')


if __name__ == '__main__':
    raise SystemExit(main())
