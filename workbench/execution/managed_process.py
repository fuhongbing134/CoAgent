"""Windows Job-contained execution, not yet wired into production.

Guarantee: ordinary descendants of this launch stay in its Job. This does NOT
cover work delegated to WMI, services or existing external daemons. No fallback
to taskkill/process groups is used. Other platforms must implement an equivalent
contract, not merely map a process-group returncode to confirmed.

The owner calls run once. cancel() sets an Event and never waits for processes or
takes a database/global registry lock. Callbacks run on the owner thread while the child
is suspended: on_created registers handles, on_running persists startup. Callers
must serialize cancellation/startup with their per-run lock (4B responsibility).
Temporary files replace pipes to avoid descendant-held EOF and reader deadlocks;
they are anonymous, parent-owned spools, not a streaming API.
"""
import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time


@dataclass(frozen=True)
class ProcessResult:
    outcome: str
    returncode: int | None
    stdout: bytes
    stderr: bytes
    exit_evidence: str
    error_type: str | None = None
    winerror: int | None = None

    @property
    def dispatch_allowed(self):
        return self.exit_evidence in {'not_started', 'confirmed'}


class _StartupInfo(C.Structure):
    _fields_ = [('cb', W.DWORD), ('lpReserved', W.LPWSTR), ('lpDesktop', W.LPWSTR),
                ('lpTitle', W.LPWSTR), ('dwX', W.DWORD), ('dwY', W.DWORD),
                ('dwXSize', W.DWORD), ('dwYSize', W.DWORD), ('dwXCountChars', W.DWORD),
                ('dwYCountChars', W.DWORD), ('dwFillAttribute', W.DWORD),
                ('dwFlags', W.DWORD), ('wShowWindow', W.WORD), ('cbReserved2', W.WORD),
                ('lpReserved2', C.c_void_p), ('hStdInput', W.HANDLE),
                ('hStdOutput', W.HANDLE), ('hStdError', W.HANDLE)]


class _StartupInfoEx(C.Structure):
    _fields_ = [('StartupInfo', _StartupInfo), ('lpAttributeList', C.c_void_p)]


class _ProcessInfo(C.Structure):
    _fields_ = [('hProcess', W.HANDLE), ('hThread', W.HANDLE),
                ('dwProcessId', W.DWORD), ('dwThreadId', W.DWORD)]


class _BasicLimits(C.Structure):
    _fields_ = [('PerProcessUserTimeLimit', C.c_int64), ('PerJobUserTimeLimit', C.c_int64),
                ('LimitFlags', W.DWORD), ('MinimumWorkingSetSize', C.c_size_t),
                ('MaximumWorkingSetSize', C.c_size_t), ('ActiveProcessLimit', W.DWORD),
                ('Affinity', C.c_size_t), ('PriorityClass', W.DWORD),
                ('SchedulingClass', W.DWORD)]


class _ExtendedLimits(C.Structure):
    _fields_ = [('BasicLimitInformation', _BasicLimits), ('IoInfo', C.c_uint64 * 6),
                ('ProcessMemoryLimit', C.c_size_t), ('JobMemoryLimit', C.c_size_t),
                ('PeakProcessMemoryUsed', C.c_size_t), ('PeakJobMemoryUsed', C.c_size_t)]


class _Accounting(C.Structure):
    _fields_ = [('Times', C.c_int64 * 4), ('TotalPageFaultCount', W.DWORD),
                ('TotalProcesses', W.DWORD), ('ActiveProcesses', W.DWORD),
                ('TotalTerminatedProcesses', W.DWORD)]


class _WinAPI:
    def __init__(self):
        if os.name != 'nt' or sys.getwindowsversion().major < 10:
            raise OSError('Windows 10+ Job List execution required')
        self.k = C.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreateJobObjectW': ([C.c_void_p, W.LPCWSTR], W.HANDLE),
            'SetInformationJobObject': ([W.HANDLE, C.c_int, C.c_void_p, W.DWORD], W.BOOL),
            'QueryInformationJobObject': ([W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.c_void_p], W.BOOL),
            'TerminateJobObject': ([W.HANDLE, W.UINT], W.BOOL),
            'InitializeProcThreadAttributeList': ([C.c_void_p, W.DWORD, W.DWORD, C.POINTER(C.c_size_t)], W.BOOL),
            'UpdateProcThreadAttribute': ([C.c_void_p, W.DWORD, C.c_size_t, C.c_void_p, C.c_size_t, C.c_void_p, C.c_void_p], W.BOOL),
            'DeleteProcThreadAttributeList': ([C.c_void_p], None),
            'CreateProcessW': ([W.LPCWSTR, W.LPWSTR, C.c_void_p, C.c_void_p, W.BOOL,
                                W.DWORD, C.c_void_p, W.LPCWSTR, C.POINTER(_StartupInfoEx),
                                C.POINTER(_ProcessInfo)], W.BOOL),
            'GetCurrentProcess': ([], W.HANDLE),
            'DuplicateHandle': ([W.HANDLE, W.HANDLE, W.HANDLE, C.POINTER(W.HANDLE), W.DWORD, W.BOOL, W.DWORD], W.BOOL),
            'GetHandleInformation': ([W.HANDLE, C.POINTER(W.DWORD)], W.BOOL),
            'IsProcessInJob': ([W.HANDLE, W.HANDLE, C.POINTER(W.BOOL)], W.BOOL),
            'ResumeThread': ([W.HANDLE], W.DWORD),
            'WaitForSingleObject': ([W.HANDLE, W.DWORD], W.DWORD),
            'GetExitCodeProcess': ([W.HANDLE, C.POINTER(W.DWORD)], W.BOOL),
            'CloseHandle': ([W.HANDLE], W.BOOL),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.k, name)
            fn.argtypes, fn.restype = args, result

    @staticmethod
    def check(value):
        if not value:
            raise C.WinError(C.get_last_error())
        return value


class ManagedProcess:
    """Single-use synchronous launch; cancellation never waits for process exit.

    on_stage is an observation/fault-injection hook; it must return promptly.
    All callbacks are trusted, bounded owner operations, not CLI-supplied code.
    No handle is exposed to the child except duplicated stdin/stdout/stderr.
    """
    def __init__(self):
        self._cancel = threading.Event()
        self._resume_lock = threading.Lock()
        self._used = False
        self.api = None
        self.job_handle = None
        self.info = _ProcessInfo()

    @property
    def pid(self):
        return int(self.info.dwProcessId) or None

    def cancel(self):
        # Linearize the final resume decision against accepting cancellation.
        # No DB calls/callbacks/long waits may run under this private lock.
        with self._resume_lock:
            self._cancel.set()

    def _active(self):
        accounting = _Accounting()
        self.api.check(self.api.k.QueryInformationJobObject(
            self.job_handle, 1, C.byref(accounting), C.sizeof(accounting), None))
        return accounting.ActiveProcesses

    def _poll(self):
        value = self.api.k.WaitForSingleObject(self.info.hProcess, 0)
        if value == 0xFFFFFFFF:
            raise C.WinError(C.get_last_error())
        if value == 258:
            return None
        if value != 0:
            raise OSError('Unexpected process wait result')
        code = W.DWORD()
        self.api.check(self.api.k.GetExitCodeProcess(self.info.hProcess, C.byref(code)))
        return code.value

    def _empty(self):
        return self._poll() is not None and self._active() == 0

    def _cleanup(self, seconds):
        # Successful TerminateJobObject is only a request, never exit evidence.
        try:
            self.api.check(self.api.k.TerminateJobObject(self.job_handle, 1))
            deadline = time.monotonic() + seconds
            while True:
                if self._empty():
                    return True
                if time.monotonic() >= deadline:
                    return False
                time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        except OSError:
            return False

    def _create(self, command, cwd, env, files):
        import msvcrt
        k, check = self.api.k, self.api.check
        self.job_handle = check(k.CreateJobObjectW(None, None))
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE, no breakaway
        check(k.SetInformationJobObject(self.job_handle, 9, C.byref(limits), C.sizeof(limits)))
        flags = W.DWORD()
        check(k.GetHandleInformation(self.job_handle, C.byref(flags)))
        if flags.value & 1:
            raise OSError('Job handle must not be inheritable')
        size = C.c_size_t()
        k.InitializeProcThreadAttributeList(None, 2, 0, C.byref(size))
        attributes = C.create_string_buffer(size.value)
        check(k.InitializeProcThreadAttributeList(attributes, 2, 0, C.byref(size)))
        duplicates = []
        try:
            current = k.GetCurrentProcess()
            for file in files:
                duplicate = W.HANDLE()
                check(k.DuplicateHandle(current, msvcrt.get_osfhandle(file.fileno()), current,
                                        C.byref(duplicate), 0, True, 2))
                duplicates.append(duplicate.value)
            jobs = (W.HANDLE * 1)(self.job_handle)
            handles = (W.HANDLE * 3)(*duplicates)
            check(k.UpdateProcThreadAttribute(attributes, 0, 0x2000D, jobs, C.sizeof(jobs), None, None))
            check(k.UpdateProcThreadAttribute(attributes, 0, 0x20002, handles, C.sizeof(handles), None, None))
            startup = _StartupInfoEx()
            startup.StartupInfo.cb = C.sizeof(startup)
            startup.StartupInfo.dwFlags = 0x100 | 1  # USESTDHANDLES | USESHOWWINDOW
            startup.StartupInfo.wShowWindow = 0
            startup.StartupInfo.hStdInput, startup.StartupInfo.hStdOutput, startup.StartupInfo.hStdError = duplicates
            startup.lpAttributeList = C.cast(attributes, C.c_void_p)
            block = None
            if env is not None:
                entries = []
                for key, value in sorted(env.items(), key=lambda pair: pair[0].upper()):
                    if not isinstance(key, str) or not isinstance(value, str) or '\0' in key + value or '=' in key:
                        raise ValueError('Invalid environment entry')
                    entries.append(key + '=' + value)
                block = C.create_unicode_buffer('\0'.join(entries) + '\0\0')
            args = C.create_unicode_buffer(subprocess.list2cmdline(command))
            check(k.CreateProcessW(command[0], args, None, None, True,
                                   0x80000 | 4 | 0x400 | 0x08000000,  # EXTENDED | SUSPENDED | UNICODE | NO_WINDOW
                                   block, cwd, C.byref(startup), C.byref(self.info)))
            inside = W.BOOL()
            check(k.IsProcessInJob(self.info.hProcess, self.job_handle, C.byref(inside)))
            if not inside.value:
                raise OSError('Creation-time Job membership missing')
        finally:
            for handle in duplicates:
                k.CloseHandle(handle)
            k.DeleteProcThreadAttributeList(attributes)

    def run(self, command, *, input=b'', cwd=None, env=None, timeout=300,
            settle_timeout=5, cleanup_timeout=5, on_created=None, on_running=None,
            cancelled=None, on_stage=None, capture_limit=16 * 1024 * 1024):
        """Only absolute executable paths; caller resolves .cmd/shims explicitly.

        confirmed certifies this Job only. Unsupported OS/creation failure returns
        not_started. Cleanup/query uncertainty returns unknown, blocking dispatch.
        No process/Job handles remain open after return, even on cleanup failure;
        kill-on-close remains a final attempt, not a confirmation.
        """
        if self._used:
            raise ValueError('ManagedProcess is single-use')
        self._used = True
        for seconds in (timeout, settle_timeout, cleanup_timeout):
            if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
                raise ValueError('Finite nonnegative timeout required')
        if not command or not all(isinstance(s, str) and '\0' not in s for s in command) or not Path(command[0]).is_absolute():
            raise ValueError('Absolute executable and valid arguments required')
        if type(capture_limit) is not int or capture_limit < 1:
            raise ValueError('Positive capture limit required')
        data = input.encode('utf-8') if isinstance(input, str) else input
        if not isinstance(data, bytes):
            raise ValueError('input must be bytes or UTF-8 text')
        cwd = str(Path(cwd).resolve(strict=True)) if cwd is not None else None
        is_cancelled = lambda: self._cancel.is_set() or bool(cancelled and cancelled())
        stage = lambda name: on_stage(name, self) if on_stage else None
        outcome, evidence, error_type, winerror, code = 'failed', 'not_started', None, None, None
        with tempfile.TemporaryFile() as stdin, tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            stdin.write(data)
            stdin.flush()
            stdin.seek(0)
            try:
                self.api = _WinAPI()
                stage('before_create')
                if is_cancelled():
                    outcome = 'cancelled'
                else:
                    self._create(command, cwd, env, (stdin, stdout, stderr))
                    evidence = 'unknown'
                    stage('after_create')
                    if on_created:
                        on_created(self)
                    if is_cancelled():
                        outcome = 'cancelled'
                    else:
                        if on_running:
                            on_running(self)
                        stage('before_resume')
                        with self._resume_lock:
                            # The external predicate must be a nonblocking memory
                            # check; never query DB or acquire the caller's locks.
                            should_resume = not is_cancelled()
                            if should_resume and self.api.k.ResumeThread(self.info.hThread) == 0xFFFFFFFF:
                                raise C.WinError(C.get_last_error())
                        if not should_resume:
                            outcome = 'cancelled'
                        else:
                            stage('after_resume')
                            deadline = time.monotonic() + timeout
                            root_exit_at = None
                            while True:
                                if is_cancelled():
                                    outcome = 'cancelled'
                                    break
                                code = self._poll()
                                if code is not None:
                                    if self._active() == 0:
                                        evidence = 'confirmed'
                                        outcome = 'completed' if code == 0 else 'failed'
                                        break
                                    root_exit_at = root_exit_at or time.monotonic()
                                    if time.monotonic() - root_exit_at >= settle_timeout:
                                        error_type = 'DescendantsStillActive'
                                        break
                                if time.monotonic() >= deadline:
                                    outcome, error_type = 'timeout', 'ExecutionTimeout'
                                    break
                                time.sleep(0.01)
            except Exception as error:
                error_type, winerror = type(error).__name__, getattr(error, 'winerror', None)
            finally:
                try:
                    if self.info.hProcess and evidence != 'confirmed':
                        evidence = 'confirmed' if self._cleanup(cleanup_timeout) else 'unknown'
                    if self.info.hProcess:
                        try:
                            code = self._poll()
                        except OSError:
                            evidence = 'unknown'
                finally:
                    if self.api:
                        for handle in (self.info.hThread, self.info.hProcess, self.job_handle):
                            if handle:
                                self.api.k.CloseHandle(handle)
                        self.info.hThread = self.info.hProcess = self.job_handle = None
            streams = []
            for file in (stdout, stderr):
                file.seek(0)
                chunk = file.read(capture_limit + 1)
                if len(chunk) > capture_limit:
                    outcome, error_type = 'failed', 'OutputLimitExceeded'
                streams.append(chunk[:capture_limit])
        return ProcessResult(outcome, code, *streams, evidence, error_type, winerror)
