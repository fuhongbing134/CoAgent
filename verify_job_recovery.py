"""Standalone Windows Job recovery experiment. Run manually; no production imports.

Exit 0: experiment assertions passed (including expected UNKNOWN windows).
Exit 1: assertion/error. Exit 2: required observation unavailable.
Neither 0 nor an absent named Job authorizes production recovery.
"""
import argparse
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid


class Startup(C.Structure):
    _fields_ = [('cb', W.DWORD), ('reserved', W.LPWSTR), ('desktop', W.LPWSTR),
                ('title', W.LPWSTR), ('x', W.DWORD), ('y', W.DWORD),
                ('xs', W.DWORD), ('ys', W.DWORD), ('xc', W.DWORD), ('yc', W.DWORD),
                ('fill', W.DWORD), ('flags', W.DWORD), ('show', W.WORD),
                ('reserved_size', W.WORD), ('reserved_ptr', C.c_void_p),
                ('stdin', W.HANDLE), ('stdout', W.HANDLE), ('stderr', W.HANDLE)]


class StartupEx(C.Structure):
    _fields_ = [('startup', Startup), ('attributes', C.c_void_p)]


class ProcessInfo(C.Structure):
    _fields_ = [('process', W.HANDLE), ('thread', W.HANDLE),
                ('pid', W.DWORD), ('tid', W.DWORD)]


class BasicLimits(C.Structure):
    _fields_ = [('process_time', C.c_int64), ('job_time', C.c_int64),
                ('flags', W.DWORD), ('min_ws', C.c_size_t), ('max_ws', C.c_size_t),
                ('active_limit', W.DWORD), ('affinity', C.c_size_t),
                ('priority', W.DWORD), ('scheduling', W.DWORD)]


class ExtendedLimits(C.Structure):
    _fields_ = [('basic', BasicLimits), ('io', C.c_uint64 * 6),
                ('process_memory', C.c_size_t), ('job_memory', C.c_size_t),
                ('peak_process', C.c_size_t), ('peak_job', C.c_size_t)]


class Accounting(C.Structure):
    _fields_ = [('times', C.c_int64 * 4), ('faults', W.DWORD),
                ('total', W.DWORD), ('active', W.DWORD), ('terminated', W.DWORD)]


class SecurityAttributes(C.Structure):
    _fields_ = [('length', W.DWORD), ('descriptor', C.c_void_p), ('inherit', W.BOOL)]


class API:
    def __init__(self):
        self.k = C.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreateJobObjectW': ([C.c_void_p, W.LPCWSTR], W.HANDLE),
            'OpenJobObjectW': ([W.DWORD, W.BOOL, W.LPCWSTR], W.HANDLE),
            'SetInformationJobObject': ([W.HANDLE, C.c_int, C.c_void_p, W.DWORD], W.BOOL),
            'QueryInformationJobObject': ([W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.c_void_p], W.BOOL),
            'IsProcessInJob': ([W.HANDLE, W.HANDLE, C.POINTER(W.BOOL)], W.BOOL),
            'InitializeProcThreadAttributeList': ([C.c_void_p, W.DWORD, W.DWORD, C.POINTER(C.c_size_t)], W.BOOL),
            'UpdateProcThreadAttribute': ([C.c_void_p, W.DWORD, C.c_size_t, C.c_void_p, C.c_size_t, C.c_void_p, C.c_void_p], W.BOOL),
            'DeleteProcThreadAttributeList': ([C.c_void_p], None),
            'CreateProcessW': ([W.LPCWSTR, W.LPWSTR, C.c_void_p, C.c_void_p, W.BOOL, W.DWORD, C.c_void_p, W.LPCWSTR, C.POINTER(StartupEx), C.POINTER(ProcessInfo)], W.BOOL),
            'ResumeThread': ([W.HANDLE], W.DWORD),
            'OpenProcess': ([W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
            'GetProcessTimes': ([W.HANDLE] + [C.POINTER(W.FILETIME)] * 4, W.BOOL),
            'WaitForSingleObject': ([W.HANDLE, W.DWORD], W.DWORD),
            'TerminateProcess': ([W.HANDLE, W.UINT], W.BOOL),
            'CloseHandle': ([W.HANDLE], W.BOOL),
            'LocalFree': ([C.c_void_p], C.c_void_p),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.k, name)
            fn.argtypes, fn.restype = args, result

    def check(self, value):
        if not value:
            raise C.WinError(C.get_last_error())
        return value

    def close(self, handle):
        if handle:
            self.k.CloseHandle(handle)

    def opened(self, name):
        C.set_last_error(0)
        handle = self.k.OpenJobObjectW(4, False, name)  # QUERY only, never create
        error = 0 if handle else C.get_last_error()  # capture before other APIs
        self.close(handle)
        return {'opened': bool(handle), 'error': error}

    def create(self, name, security=None):
        C.set_last_error(0)
        handle = self.k.CreateJobObjectW(security, name)
        error = C.get_last_error()
        self.check(handle)
        if error == 183:
            self.close(handle)
            raise RuntimeError('Name collision; refusing existing Job')
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE, no breakaway
        try:
            self.check(self.k.SetInformationJobObject(handle, 9, C.byref(limits), C.sizeof(limits)))
            return handle
        except BaseException:
            self.close(handle)
            raise

    def active(self, job):
        value = Accounting()
        self.check(self.k.QueryInformationJobObject(job, 1, C.byref(value), C.sizeof(value), None))
        return value.active

    def birth(self, handle):
        times = [W.FILETIME() for _ in range(4)]
        self.check(self.k.GetProcessTimes(handle, *(C.byref(t) for t in times)))
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime

    def process(self, pid):
        return self.check(self.k.OpenProcess(0x100000 | 0x1000, False, pid))

    def launch(self, job, folder, short):
        size = C.c_size_t()
        self.k.InitializeProcThreadAttributeList(None, 1, 0, C.byref(size))
        buf = C.create_string_buffer(size.value)
        self.check(self.k.InitializeProcThreadAttributeList(buf, 1, 0, C.byref(size)))
        info = ProcessInfo()
        try:
            jobs = (W.HANDLE * 1)(job)
            self.check(self.k.UpdateProcThreadAttribute(buf, 0, 0x2000D, jobs, C.sizeof(jobs), None, None))
            startup = StartupEx()
            startup.startup.cb = C.sizeof(startup)
            startup.attributes = C.cast(buf, C.c_void_p)
            cmd = command('--tree', str(folder), '0', 'short' if short else 'long')
            self.check(self.k.CreateProcessW(sys.executable, C.create_unicode_buffer(subprocess.list2cmdline(cmd)),
                       None, None, False, 0x80000 | 4 | 0x08000000, None, str(folder), C.byref(startup), C.byref(info)))
            inside = W.BOOL()
            self.check(self.k.IsProcessInJob(info.process, job, C.byref(inside)))
            if not inside.value:
                raise RuntimeError('Root not created inside Job')
            return info
        except BaseException:
            if info.process:
                self.k.TerminateProcess(info.process, 1)
            self.close(info.thread)
            self.close(info.process)
            raise
        finally:
            self.k.DeleteProcThreadAttributeList(buf)


def command(*args):
    return [sys.executable, '-B', str(Path(__file__).resolve()), *args]


def save(path, value):
    temp = path.with_suffix('.writing')
    with temp.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def until(predicate, seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise TimeoutError('Bounded observation timed out')


def tree(folder, depth, short):
    api = API()
    handle = api.process(os.getpid())
    try:
        save(folder / ('node%d.json' % depth), {'pid': os.getpid(), 'birth': api.birth(handle)})
    finally:
        api.close(handle)
    child = None
    try:
        if depth < 2:
            child = subprocess.Popen(command('--tree', str(folder), str(depth + 1), 'short' if short else 'long'),
                                     close_fds=True, creationflags=0x08000000)
        time.sleep(6 if short else 45)  # watchdog: no indefinite orphan sleepers
        if child:
            child.wait(timeout=8)
    finally:
        if child and child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def manager(folder, window):
    api = API()
    record = read(folder / 'intent.json')
    job, info = None, None
    try:
        if window != 'before_create':
            job = api.create(record['job'])
            info = api.launch(job, folder, window == 'empty')
            if window != 'before_registration':
                record.update(created=True, kill_on_close=True, root={'pid': info.pid, 'birth': api.birth(info.process)},
                              membership_verified=True)
                save(folder / 'created.json', record)
            if window in ('running', 'running_unique', 'empty'):
                if api.k.ResumeThread(info.thread) == 0xFFFFFFFF:
                    raise C.WinError(C.get_last_error())
                until(lambda: all((folder / ('node%d.json' % d)).exists() for d in range(3)))
                # Verify ordinary descendants, not just root membership.
                for depth in range(3):
                    node = read(folder / ('node%d.json' % depth))
                    handle = api.process(node['pid'])
                    try:
                        inside = W.BOOL()
                        api.check(api.k.IsProcessInJob(handle, job, C.byref(inside)))
                        if api.birth(handle) != node['birth'] or not inside.value:
                            raise RuntimeError('Descendant identity/membership mismatch')
                    finally:
                        api.close(handle)
                if window == 'empty':
                    save(folder / 'tree_ready.json', {'ready': True})
                    until(lambda: (folder / 'observer_handles.json').exists())
                    until(lambda: api.active(job) == 0)
        save(folder / 'ready.json', {'stage': window})
        time.sleep(60)  # controller normally terminates us here, without finally
    finally:
        if info:
            api.close(info.thread)
            api.close(info.process)
        api.close(job)


def observe(folder):
    """Fresh process: durable records + same-identity process queries, no Job hold."""
    api = API()
    intent = read(folder / 'intent.json')
    created = read(folder / 'created.json') if (folder / 'created.json').exists() else None
    nodes = [read(p) for p in sorted(folder.glob('node*.json'))]
    if created and not nodes:
        nodes = [created['root']]
    states = []
    for node in nodes:
        C.set_last_error(0)
        handle = api.k.OpenProcess(0x100000 | 0x1000, False, node['pid'])
        error = 0 if handle else C.get_last_error()
        try:
            if not handle:
                state = 'pid_absent' if error == 87 else 'unknown'
            elif api.birth(handle) != node['birth']:
                state = 'pid_reused'
            else:
                wait = api.k.WaitForSingleObject(handle, 0)
                state = 'exited' if wait == 0 else ('alive' if wait == 258 else 'unknown')
            states.append(dict(node, state=state, error=error))
        finally:
            api.close(handle)
    opened = api.opened(intent['job'])
    bound = bool(created and all(created.get(k) == intent[k] for k in ('attempt', 'job', 'window'))
                 and created.get('membership_verified') and created.get('kill_on_close'))
    candidate = bool(bound and states and all(n['state'] in ('exited', 'pid_absent', 'pid_reused') for n in states)
                     and not opened['opened'] and opened['error'] == 2)
    return {'open': opened, 'created_record_matches': bound, 'processes': states,
            'controlled_candidate': candidate, 'production_release_authorized': False,
            'note': 'Finite known tree + durable record; not arbitrary descendant enumeration or production proof.'}


def run_probe(output):
    api = API()
    results = []

    def emit(name, status, **details):
        row = dict(case=name, status=status, **details)
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        save(output / 'report.json', {'results': results, 'production_verified': False})

    # Empty Job cases: no worker processes and no production object names.
    name = 'Local\\Task6Probe_' + uuid.uuid4().hex
    job = api.create(name)
    try:
        opened = api.opened(name)
        emit('existing_accessible', 'PASS' if opened['opened'] else 'FAIL', observation=opened)
        wrong = api.opened(name + '_wrong_scope_target')
        emit('wrong_name_target', 'PASS' if not wrong['opened'] else 'FAIL', observation=wrong,
             limitation='Wrong name simulates lookup error; cross-logon namespace not exercised.')
    finally:
        api.close(job)
    destroyed = api.opened(name)
    never = api.opened('Local\\Task6Probe_' + uuid.uuid4().hex)
    emit('destroyed_vs_never_created', 'PASS' if not destroyed['opened'] and not never['opened'] else 'FAIL',
         destroyed=destroyed, never_created=never, same_error=destroyed['error'] == never['error'])
    # A real access-denied Job, not an injected/fabricated error code.
    descriptor, denied_job = C.c_void_p(), None
    try:
        adv = C.WinDLL('advapi32', use_last_error=True)
        convert = adv.ConvertStringSecurityDescriptorToSecurityDescriptorW
        convert.argtypes = [W.LPCWSTR, W.DWORD, C.POINTER(C.c_void_p), C.c_void_p]
        convert.restype = W.BOOL
        api.check(convert('D:(D;;0x0004;;;WD)', 1, C.byref(descriptor), None))
        security = SecurityAttributes(C.sizeof(SecurityAttributes), descriptor, False)
        denied_name = 'Local\\Task6Probe_' + uuid.uuid4().hex
        denied_job = api.create(denied_name, C.byref(security))
        denied = api.opened(denied_name)
        emit('access_denied', 'PASS' if not denied['opened'] and denied['error'] == 5 else 'UNKNOWN', observation=denied)
    except OSError as error:
        emit('access_denied', 'UNKNOWN', error=getattr(error, 'winerror', None), detail=str(error))
    finally:
        api.close(denied_job)
        if descriptor:
            api.k.LocalFree(descriptor)

    for window in ('before_create', 'before_registration', 'before_resume', 'running_unique', 'running', 'empty'):
        folder = output / window
        folder.mkdir()
        intent = {'attempt': uuid.uuid4().hex, 'job': 'Local\\Task6Probe_' + uuid.uuid4().hex,
                  'window': window}
        save(folder / 'intent.json', intent)
        proc, handles, extra = None, [], None
        try:
            with (folder / 'manager.log').open('wb') as log:
                proc = subprocess.Popen(command('--manager', str(folder), window), stdin=subprocess.DEVNULL,
                                        stdout=log, stderr=log, close_fds=True, creationflags=0x08000000)
                marker = 'tree_ready.json' if window == 'empty' else 'ready.json'
                until(lambda: (folder / marker).exists() or proc.poll() is not None)
                if not (folder / marker).exists():
                    raise RuntimeError('Manager failed; inspect manager.log')
                nodes = [read(p) for p in folder.glob('node*.json')]
                if not nodes and (folder / 'created.json').exists():
                    nodes = [read(folder / 'created.json')['root']]
                for node in nodes:
                    handle = api.process(node['pid'])
                    handles.append(handle)
                    if api.birth(handle) != node['birth']:
                        raise RuntimeError('PID reuse before crash observation')
                if window == 'empty':
                    save(folder / 'observer_handles.json', {'ready': True})
                    until(lambda: (folder / 'ready.json').exists() or proc.poll() is not None)
                    if not (folder / 'ready.json').exists():
                        raise RuntimeError('Manager failed before empty stage')
                if window == 'running':
                    # Control: an extra Job handle prevents kill-on-last-close.
                    extra = api.check(api.k.OpenJobObjectW(4, False, intent['job']))
                proc.kill()  # exact Popen handle, never PID/name-based broad killing
                proc.wait(timeout=8)
                if extra:
                    alive = all(api.k.WaitForSingleObject(h, 0) == 258 for h in handles)
                    emit('extra_handle_prevents_last_close', 'PASS' if alive else 'FAIL', error=0)
                    api.close(extra)
                    extra = None
                until(lambda: all(api.k.WaitForSingleObject(h, 0) == 0 for h in handles))
                # New observer process does not inherit these process handles.
                child = subprocess.run(command('--observe', str(folder)), capture_output=True, text=True,
                                       encoding='utf-8', timeout=15, creationflags=0x08000000)
                if child.returncode:
                    raise RuntimeError(child.stderr)
                observation = json.loads(child.stdout)
                if window in ('before_create', 'before_registration'):
                    emit(window, 'UNKNOWN', expected_unknown=True, observation=observation,
                         reason='No durable created/membership record; absence cannot certify not_started or exit.')
                else:
                    emit(window, 'PASS' if observation['controlled_candidate'] else 'FAIL', observation=observation,
                         independent_handle_waits_confirmed=True)
        except Exception as error:
            emit(window, 'FAIL', detail=str(error), error=getattr(error, 'winerror', None))
        finally:
            if proc and proc.poll() is None:
                proc.kill()
                proc.wait(timeout=8)
            api.close(extra)
            for handle in handles:
                api.close(handle)
    failures = any(r['status'] == 'FAIL' for r in results)
    incomplete = any(r['status'] == 'UNKNOWN' and not r.get('expected_unknown') for r in results)
    code = 1 if failures else (2 if incomplete else 0)
    save(output / 'report.json', {'results': results, 'exit_code': code, 'production_verified': False,
                                'python': sys.version, 'windows': str(sys.getwindowsversion()),
                                'cross_logon_namespace_tested': False})
    print('RESULT exit_code=%d production_verified=false report=%s' % (code, output / 'report.json'), flush=True)
    return code


def main():
    if os.name != 'nt' or sys.getwindowsversion().major < 10:
        print('UNKNOWN: Windows 10+ required; no experiment executed.')
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manager', nargs=2, metavar=('DIR', 'WINDOW'), help=argparse.SUPPRESS)
    parser.add_argument('--tree', nargs=3, help=argparse.SUPPRESS)
    parser.add_argument('--observe', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.manager:
        manager(Path(args.manager[0]), args.manager[1])
        return 0
    if args.tree:
        tree(Path(args.tree[0]), int(args.tree[1]), args.tree[2] == 'short')
        return 0
    if args.observe:
        print(json.dumps(observe(Path(args.observe))))
        return 0
    output = Path(tempfile.mkdtemp(prefix='task6-job-probe-'))
    print('Evidence directory: ' + str(output), flush=True)
    try:
        return run_probe(output)
    except Exception as error:
        save(output / 'fatal.json', {'error': str(error), 'production_verified': False})
        print('FAIL: ' + str(error), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
