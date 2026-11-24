"""Evidence from the selected Codex CLI and the live processes of its invocation."""

import base64
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import subprocess


MXC_PROCESS_PROBE = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$cliId = CLI_PID
$created = [long]'CLI_CREATED'
$cli = [Diagnostics.Process]::GetProcessById($cliId)
if ($cli.StartTime.ToFileTimeUtc() -ne $created) { return }
$image = (Get-CimInstance Win32_Process -Filter "ProcessId = $cliId").ExecutablePath
if (-not $image) { return }
while (-not $cli.HasExited) {
    foreach ($helper in Get-CimInstance Win32_Process -Filter "ParentProcessId = $cliId") {
        if ($helper.ExecutablePath -ne $image -or -not $helper.CreationDate -or
            $helper.CommandLine -notmatch '^\s*(?:"[^"]+"|[^"\s]+)\s+(?:"--__codex-windows-mxc"|--__codex-windows-mxc)\s*$') { continue }
        $helperCreated = $helper.CreationDate.ToFileTimeUtc()
        # CIM rounds FILETIME values to microseconds.
        if ($helperCreated -lt ($created - ($created % 10))) { continue }
        $helperId = $helper.ProcessId
        $tool = Get-CimInstance Win32_Process -Filter "ParentProcessId = $helperId" |
            Where-Object { $_.CreationDate -and $_.CreationDate -ge $helper.CreationDate } |
            Select-Object -First 1
        if (-not $tool) { continue }
        $current = Get-CimInstance Win32_Process -Filter "ProcessId = $helperId"
        if ($current.CreationDate -ne $helper.CreationDate -or $cli.HasExited) { continue }
        [pscustomobject]@{
            executable = $image
            cli_pid = $cliId
            helper_pid = $helperId
            tool_pid = $tool.ProcessId
            cli_created_filetime = [string]$created
            helper_created_filetime = [string]$helperCreated
            tool_created_filetime = [string]$tool.CreationDate.ToFileTimeUtc()
        } | ConvertTo-Json -Compress
        return
    }
    Start-Sleep -Milliseconds 500
}
"""


class MxcProcessObservation:
    """A single CLI-scoped observation, independent of the managed job."""

    def __init__(self, executable):
        self.executable = executable
        self.observer = None
        self.cli_pid = None
        self.created = None

    def __enter__(self):
        return self

    def on_stage(self, stage, process):
        if stage != 'after_resume' or os.name != 'nt':
            return
        try:
            self.cli_pid = process.pid
            self.created = str(process.creation_filetime())
            script = MXC_PROCESS_PROBE.replace('CLI_PID', str(self.cli_pid)).replace('CLI_CREATED', self.created)
            encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
            powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / (
                r'System32\WindowsPowerShell\v1.0\powershell.exe')
            self.observer = subprocess.Popen(
                [str(powershell), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except OSError:
            return  # Observation must not affect the CLI's outcome.

    def collect(self):
        if self.observer is None:
            return None
        try:
            if self.observer.wait(timeout=0.2) != 0:
                return None
            evidence = json.load(self.observer.stdout)
            image = evidence.pop('executable')
            normalize = lambda path: os.path.normcase(os.path.normpath(path))
            if (normalize(image) == normalize(self.executable)
                    and evidence['cli_pid'] == self.cli_pid and evidence['cli_created_filetime'] == self.created):
                return {key: evidence[key] for key in (
                    'cli_pid', 'helper_pid', 'tool_pid', 'cli_created_filetime',
                    'helper_created_filetime', 'tool_created_filetime')}
        except (OSError, subprocess.TimeoutExpired, TypeError, ValueError, KeyError, AttributeError):
            pass
        return None

    def __exit__(self, *_):
        if self.observer is None:
            return
        try:
            if self.observer.poll() is None:
                self.observer.kill()
                self.observer.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            pass
        finally:
            self.observer.stdout.close()


@lru_cache(maxsize=16)
def cli_version(executable):
    try:
        result = subprocess.run(
            [executable, '--version'], capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=5, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return 'unknown'
    match = re.fullmatch(r'codex(?:-cli)? ([0-9][\w.+-]*)', result.stdout.strip())
    return match[1] if match else 'unknown'


def select_backend(stderr, process_evidence=None):
    selections = {value.strip() for value in re.findall(r'Windows sandbox selection:([^\r\n]*)', stderr)}
    if not selections and process_evidence is not None:
        return 'mxc', 'process_ancestry'
    if len(selections) == 1 and selections <= {'mxc', 'elevated', 'unelevated'}:
        backend = selections.pop()
        if process_evidence is None or backend == 'mxc':
            return backend, 'cli_stderr'
    return 'unknown', 'unknown'


def observed_backend(stderr, requested=None):
    backend, source = select_backend(stderr)
    if requested is None or requested == backend:
        return backend, source
    return 'unknown', 'unknown'
