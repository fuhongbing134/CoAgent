"""Real Windows processes and OS handles; no production DB/backend sessions.

These tests establish Job-local evidence for controlled fixtures, not the absence
of WMI/service/daemon delegation in real agent backends.
"""
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from managed_process import ManagedProcess, _WinAPI


def wait_for(predicate, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError('Controlled process condition timed out')


@unittest.skipUnless(os.name == 'nt', 'Windows Job API tests')
class ManagedProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.api = _WinAPI()
        self.api.k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        self.api.k.OpenProcess.restype = W.HANDLE

    def command(self, source):
        return [sys.executable, '-B', '-c', source]

    def run_code(self, source, **kwargs):
        return ManagedProcess().run(self.command(source), timeout=5, **kwargs)

    def hold_process(self, pid):
        handle = self.api.check(self.api.k.OpenProcess(0x100000, False, pid))
        self.addCleanup(self.api.k.CloseHandle, handle)
        return handle

    def assert_exited(self, handle):
        self.assertEqual(self.api.k.WaitForSingleObject(handle, 8000), 0)

    def tree_source(self, pidfile, depth=2, root_exits=True, pipes=False):
        # Every child publishes before spawning the next; root waits for all PIDs.
        leaf = "import os,sys,time;open(sys.argv[1],'a').write(str(os.getpid())+'\\n');time.sleep(30)"
        code = leaf
        for _ in range(depth - 1):
            code = ("import os,sys,time,subprocess;open(sys.argv[1],'a').write(str(os.getpid())+'\\n');"
                    f"subprocess.Popen([sys.executable,'-B','-c',{code!r},sys.argv[1]]);time.sleep(30)")
        root = ("import subprocess,sys,time,pathlib;"
                f"p=pathlib.Path({str(pidfile)!r});"
                f"c=subprocess.Popen([sys.executable,'-B','-c',{code!r},str(p)]"
                + (",stdout=subprocess.PIPE,stderr=subprocess.PIPE" if pipes else '') + ");"
                "deadline=time.monotonic()+5;"
                f"\nwhile (not p.exists() or len(p.read_text().splitlines())<{depth}) and time.monotonic()<deadline: time.sleep(.01)\n"
                + ("time.sleep(30)" if not root_exits else "print('root done',flush=True)"))
        return root

    def test_stdio_unicode_environment_and_nonzero(self):
        result = self.run_code("import sys,os;print(sys.stdin.read());print(os.environ['JOB_TEST']);"
                               "print('err',file=sys.stderr);sys.exit(7)",
                               input='中文 input', env={**os.environ, 'JOB_TEST': 'value'}, cwd=self.directory)
        self.assertEqual((result.outcome, result.returncode, result.exit_evidence), ('failed', 7, 'confirmed'))
        self.assertIn('中文 input', result.stdout.decode('utf-8'))
        self.assertIn(b'value', result.stdout)
        self.assertIn(b'err', result.stderr)
        self.assertTrue(result.dispatch_allowed)

    def test_creation_membership_and_suspended_callbacks(self):
        marker = self.directory / 'ran'
        observed = []

        def created(process):
            self.assertFalse(marker.exists())
            self.assertEqual(process._active(), 1)
            self.assertIsNone(process._poll())
            flags = W.DWORD()
            self.api.check(self.api.k.GetHandleInformation(process.job_handle, C.byref(flags)))
            self.assertFalse(flags.value & 1)
            observed.append('registered')

        def running(process):
            self.assertFalse(marker.exists())
            observed.append('running')

        result = self.run_code(f"open({str(marker)!r},'w').write('ran')",
                               on_created=created, on_running=running)
        self.assertEqual(observed, ['registered', 'running'])
        self.assertTrue(marker.exists())
        self.assertEqual(result.exit_evidence, 'confirmed')

    def test_root_exit_does_not_hide_descendants_or_inherited_output(self):
        for pipes in (False, True):
            with self.subTest(pipes=pipes):
                pidfile = self.directory / f'tree-{pipes}'
                handles = []

                def observed(stage, process):
                    if stage == 'after_resume':
                        wait_for(lambda: pidfile.exists() and len(pidfile.read_text().splitlines()) == 2)
                        handles.extend(self.hold_process(int(pid)) for pid in pidfile.read_text().splitlines())

                result = self.run_code(self.tree_source(pidfile, pipes=pipes), on_stage=observed,
                                       settle_timeout=.1, cleanup_timeout=2)
                self.assertEqual((result.outcome, result.exit_evidence, result.error_type),
                                 ('failed', 'confirmed', 'DescendantsStillActive'))
                self.assertEqual(len(handles), 2)
                for handle in handles:
                    self.assert_exited(handle)

    def test_timeout_cleans_multilevel_tree(self):
        pidfile = self.directory / 'timeout-tree'
        handles = []

        def observe(stage, process):
            if stage == 'after_resume':
                wait_for(lambda: pidfile.exists() and len(pidfile.read_text().splitlines()) == 2)
                handles.extend(self.hold_process(int(pid)) for pid in pidfile.read_text().splitlines())

        result = ManagedProcess().run(self.command(self.tree_source(pidfile, root_exits=False)),
                                      timeout=.2, cleanup_timeout=2, on_stage=observe)
        self.assertEqual((result.outcome, result.exit_evidence), ('timeout', 'confirmed'))
        for handle in handles:
            self.assert_exited(handle)

    def test_cancellation_at_each_start_boundary(self):
        for boundary in ('before_create', 'after_create', 'before_resume', 'after_resume'):
            with self.subTest(boundary=boundary):
                process = ManagedProcess()
                seen = []

                def observe(stage, owner):
                    if stage == boundary:
                        if owner.pid:
                            seen.append(self.hold_process(owner.pid))
                        owner.cancel()

                result = process.run(self.command('import time;time.sleep(30)'), on_stage=observe)
                self.assertEqual(result.outcome, 'cancelled')
                self.assertEqual(result.exit_evidence, 'not_started' if boundary == 'before_create' else 'confirmed')
                for handle in seen:
                    self.assert_exited(handle)

    def test_cross_thread_cancel_after_root_exit(self):
        pidfile = self.directory / 'cancel-tree'
        process = ManagedProcess()
        timer = threading.Timer(.5, process.cancel)
        timer.start()
        try:
            result = process.run(self.command(self.tree_source(pidfile)), timeout=5, settle_timeout=3)
        finally:
            timer.cancel()
            timer.join(timeout=2)
        self.assertEqual((result.outcome, result.exit_evidence), ('cancelled', 'confirmed'))

    def test_registration_and_running_write_failure_cleanup_suspended_child(self):
        for name in ('on_created', 'on_running'):
            with self.subTest(callback=name):
                seen = []

                def fail(process):
                    seen.append(self.hold_process(process.pid))
                    raise RuntimeError('injected registration/database failure')

                result = self.run_code('import time;time.sleep(30)', **{name: fail})
                self.assertEqual((result.outcome, result.exit_evidence, result.error_type),
                                 ('failed', 'confirmed', 'RuntimeError'))
                self.assert_exited(seen[0])

    def test_query_failure_cannot_claim_confirmed(self):
        process = ManagedProcess()
        handles = []
        with patch.object(process, '_active', side_effect=OSError('injected query failure')):
            result = process.run(self.command('pass'), cleanup_timeout=.1,
                                 on_created=lambda owner: handles.append(self.hold_process(owner.pid)))
        self.assertEqual(result.exit_evidence, 'unknown')
        self.assertFalse(result.dispatch_allowed)
        self.assert_exited(handles[0])

    def test_cleanup_failure_retains_unknown_even_if_close_kills_child(self):
        process = ManagedProcess()
        handles = []
        with patch.object(process, '_cleanup', return_value=False):
            def registered(owner):
                handles.append(self.hold_process(owner.pid))
                owner.cancel()
            result = process.run(self.command('import time;time.sleep(30)'), on_created=registered)
        self.assertEqual((result.outcome, result.exit_evidence), ('cancelled', 'unknown'))
        self.assertFalse(result.dispatch_allowed)
        self.assert_exited(handles[0])

    def test_breakaway_is_rejected(self):
        marker = self.directory / 'escaped'
        child = f"open({str(marker)!r},'w').write('escaped')"
        source = ("import subprocess,sys;"
                  f"\ntry: subprocess.Popen([sys.executable,'-B','-c',{child!r}],creationflags=0x01000000)\n"
                  "except OSError: print('blocked')\n"
                  "else: sys.exit(6)\n")
        result = self.run_code(source)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, b'blocked'))
        self.assertFalse(marker.exists())

    def test_job_list_or_creation_failure_has_no_fallback(self):
        process = ManagedProcess()
        with patch('managed_process._WinAPI', side_effect=OSError('unsupported platform')):
            result = process.run(self.command('pass'))
        self.assertEqual((result.outcome, result.exit_evidence, process.pid), ('failed', 'not_started', None))
        result = self.run_code('pass', env={'bad=key': 'value'})
        self.assertEqual((result.outcome, result.exit_evidence), ('failed', 'not_started'))
        missing = ManagedProcess().run([str(self.directory / 'missing.exe')])
        self.assertEqual((missing.outcome, missing.exit_evidence), ('failed', 'not_started'))

    def test_crash_supervisor_at_creation_and_resume_boundaries(self):
        for boundary in ('before_create', 'after_create', 'before_resume', 'after_resume'):
            with self.subTest(boundary=boundary):
                marker = self.directory / (boundary + '.json')
                code = ("import json,sys,time;from pathlib import Path;from managed_process import ManagedProcess;"
                        f"marker=Path({str(marker)!r});boundary={boundary!r};"
                        "\ndef observe(stage,p):\n if stage==boundary:\n  marker.write_text(json.dumps({'pid':p.pid}))\n  time.sleep(30)\n"
                        "\nManagedProcess().run([sys.executable,'-B','-c','import time;time.sleep(30)'],on_stage=observe)\n")
                supervisor = subprocess.Popen(self.command(code), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    wait_for(marker.exists)
                    pid = json.loads(marker.read_text())['pid']
                    handle = self.hold_process(pid) if pid else None
                    supervisor.kill()
                    supervisor.wait(timeout=5)
                    if handle:
                        self.assert_exited(handle)
                finally:
                    if supervisor.poll() is None:
                        supervisor.kill()
                    supervisor.wait(timeout=5)

    def test_nested_job(self):
        nested = self.run_code("import sys;from managed_process import ManagedProcess;"
                               "r=ManagedProcess().run([sys.executable,'-B','-c','print(456)']);"
                               "print(r.outcome,r.exit_evidence,r.stdout);"
                               "sys.exit(0 if r.outcome=='completed' and r.exit_evidence=='confirmed' else 3)")
        self.assertEqual((nested.returncode, nested.exit_evidence), (0, 'confirmed'), nested.stdout)

    def test_supervisor_crash_kills_live_grandchildren(self):
        pidfile = self.directory / 'crash-tree'
        ready = self.directory / 'supervisor-ready'
        source = self.tree_source(pidfile, root_exits=False)
        code = ("import sys,time;from pathlib import Path;from managed_process import ManagedProcess;"
                f"p=Path({str(pidfile)!r});r=Path({str(ready)!r});"
                "\ndef observe(stage,owner):\n if stage=='after_resume':\n"
                "  deadline=time.monotonic()+5\n"
                "  while (not p.exists() or len(p.read_text().splitlines())<2) and time.monotonic()<deadline: time.sleep(.01)\n"
                "  r.write_text(str(owner.pid))\n  time.sleep(30)\n"
                f"\nManagedProcess().run([sys.executable,'-B','-c',{source!r}],on_stage=observe)\n")
        supervisor = subprocess.Popen(self.command(code), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            wait_for(ready.exists)
            handles = [self.hold_process(int(pid)) for pid in [ready.read_text(), *pidfile.read_text().splitlines()]]
            self.assertEqual(len(handles), 3)
            supervisor.kill()
            supervisor.wait(timeout=5)
            for handle in handles:
                self.assert_exited(handle)
        finally:
            if supervisor.poll() is None:
                supervisor.kill()
            supervisor.wait(timeout=5)

    def test_service_and_migration_lock_handles_excluded(self):
        from service_instance import ServiceInstanceLock
        from migration_barrier import MigrationBarrier
        import msvcrt
        database = self.directory / 'locks.db'
        database.touch()
        with ServiceInstanceLock(database) as instance, MigrationBarrier(database) as barrier:
            descriptors = [instance._file.fileno(), barrier.file.fileno()]
            old_flags = [os.get_inheritable(fd) for fd in descriptors]
            # Even an accidentally inheritable unrelated handle must be excluded.
            for fd in descriptors:
                os.set_inheritable(fd, True)
            handles = [msvcrt.get_osfhandle(fd) for fd in descriptors]
            observed = []

            def created(process):
                observed.append(process.job_handle)

            # Child receives numeric handles over stdin set before launch; the
            # file-path comparison avoids mistaking a reused handle for leakage.
            source = ("import ctypes as c,json,sys;from ctypes import wintypes as w;"
                      "k=c.WinDLL('kernel32',use_last_error=True);"
                      "k.GetFinalPathNameByHandleW.argtypes=[w.HANDLE,w.LPWSTR,w.DWORD,w.DWORD];"
                      "k.GetFinalPathNameByHandleW.restype=w.DWORD;"
                      "\nfor h in json.loads(sys.stdin.read()):\n"
                      " b=c.create_unicode_buffer(32768)\n"
                      " n=k.GetFinalPathNameByHandleW(h,b,len(b),0)\n"
                      " print(b.value if n else 'not inherited')\n")
            try:
                result = self.run_code(source, input=json.dumps(handles), on_created=created)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn(b'.service.lock', result.stdout)
                self.assertNotIn(b'.migration.lock', result.stdout)
                self.assertEqual(result.exit_evidence, 'confirmed')
            finally:
                for fd, flag in zip(descriptors, old_flags):
                    os.set_inheritable(fd, flag)

    def test_output_limit_is_failure_without_losing_exit_evidence(self):
        result = self.run_code("print('x'*1024)", capture_limit=32)
        self.assertEqual((result.outcome, result.exit_evidence, result.error_type),
                         ('failed', 'confirmed', 'OutputLimitExceeded'))
        self.assertEqual(len(result.stdout), 32)

    def test_unlisted_inheritable_handle_not_inherited(self):
        # Named event remains open if inherited even after parent closes its copy.
        k = self.api.k
        k.CreateEventW.argtypes = [C.c_void_p, W.BOOL, W.BOOL, W.LPCWSTR]
        k.CreateEventW.restype = W.HANDLE
        k.SetHandleInformation.argtypes = [W.HANDLE, W.DWORD, W.DWORD]
        k.SetHandleInformation.restype = W.BOOL
        import uuid
        name = 'Local\\agent-lab-test-' + uuid.uuid4().hex
        event = self.api.check(k.CreateEventW(None, True, False, name))
        self.api.check(k.SetHandleInformation(event, 1, 1))
        closed = []

        def registered(process):
            self.api.check(k.CloseHandle(event))
            closed.append(True)

        source = ("import ctypes as c;from ctypes import wintypes as w;"
                  "k=c.WinDLL('kernel32',use_last_error=True);"
                  "k.OpenEventW.argtypes=[w.DWORD,w.BOOL,w.LPCWSTR];k.OpenEventW.restype=w.HANDLE;"
                  f"h=k.OpenEventW(0x100000,False,{name!r});print(bool(h));"
                  "import sys;sys.exit(4 if h else 0)")
        try:
            result = self.run_code(source, on_created=registered)
            self.assertEqual((result.returncode, result.stdout.strip()), (0, b'False'))
        finally:
            if not closed:
                k.CloseHandle(event)


if __name__ == '__main__':
    unittest.main()
