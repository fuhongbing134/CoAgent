"""Standalone Job breakaway experiment; only manual execution after review. Run manually; no production imports.

Exit 0: all assertions passed with no UNKNOWN observations.
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
            'TerminateJobObject': ([W.HANDLE, W.UINT], W.BOOL),
            'GetTickCount64': ([], C.c_uint64),
            'ProcessIdToSessionId': ([W.DWORD, C.POINTER(W.DWORD)], W.BOOL),
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
        return self.check(self.k.OpenProcess(0x100000 | 0x1000 | 1, False, pid))

    def launch(self, job, folder, mode):
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
            cmd = command('--node', str(folder), 'root')
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



MODE = "breakaway"


def get_limits(api, job):
    value = ExtendedLimits()
    api.check(api.k.QueryInformationJobObject(job, 9, C.byref(value), C.sizeof(value), None))
    return value


def node(folder, role):
    api = API()
    intent = read(folder / 'intent.json')
    own = api.process(os.getpid())
    try:
        identity = {'pid': os.getpid(), 'birth': api.birth(own)}
    finally:
        api.close(own)
    if role == 'leaf':
        # Root still holds the Job open until this membership observation finishes.
        job = api.k.OpenJobObjectW(4, False, intent['job'])
        error = 0 if job else C.get_last_error()
        try:
            inside = W.BOOL()
            if job:
                own = api.process(os.getpid())
                try:
                    api.check(api.k.IsProcessInJob(own, job, C.byref(inside)))
                finally:
                    api.close(own)
            identity.update(in_target_job=bool(inside.value) if job else None, membership_error=error)
            save(folder / 'leaf.json', identity)
        finally:
            api.close(job)
        time.sleep(45)  # detached leaf also has an independent bounded lifetime
        return
    job, child = None, None
    try:
        C.set_last_error(0)
        job = api.k.OpenJobObjectW(2 | 4, False, intent['job'])
        error = 0 if job else C.get_last_error()
        if not job:
            save(folder / 'mutation.json', {'opened': False, 'error': error})
            return
        value = get_limits(api, job)
        old = value.basic.flags
        if MODE == 'lifecycle':
            value.basic.flags &= ~0x2000  # deliberately not restored
        else:
            value.basic.flags |= 0x800  # BREAKAWAY_OK; keep kill-on-close for root
        C.set_last_error(0)
        changed = api.k.SetInformationJobObject(job, 9, C.byref(value), C.sizeof(value))
        error = 0 if changed else C.get_last_error()
        actual = get_limits(api, job).basic.flags
        save(folder / 'mutation.json', {'opened': True, 'error': error, 'set_ok': bool(changed),
                                        'old_flags': old, 'actual_flags': actual})
        if not changed:
            return
        flags = 0x08000000 | (0x01000000 if MODE == 'breakaway' else 0)
        # Only the explicitly created leaf can escape; it never creates descendants.
        child = subprocess.Popen(command('--node', str(folder), 'leaf'), close_fds=True,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, creationflags=flags)
        until(lambda: (folder / 'leaf.json').exists() or child.poll() is not None)
        if not (folder / 'leaf.json').exists():
            raise RuntimeError('Leaf exited before membership report')
        save(folder / 'root.json', identity)
        # Crucial: drop the worker Job handle BEFORE ready; manager is last holder.
        api.close(job)
        job = None
        save(folder / 'node_ready.json', {'worker_job_handle_closed': True})
        time.sleep(45)
    finally:
        api.close(job)
        if child and child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def manager(folder):
    api = API()
    intent = read(folder / 'intent.json')
    job, info = None, None
    try:
        job = api.create(intent['job'])
        info = api.launch(job, folder, MODE)
        save(folder / 'created.json', dict(intent, root={'pid': info.pid, 'birth': api.birth(info.process)},
                                            initial_flags=get_limits(api, job).basic.flags,
                                            membership_verified=True))
        if api.k.ResumeThread(info.thread) == 0xFFFFFFFF:
            raise C.WinError(C.get_last_error())
        until(lambda: (folder / 'node_ready.json').exists() or (folder / 'mutation.json').exists()
              and not read(folder / 'mutation.json').get('set_ok'))
        if not (folder / 'node_ready.json').exists():
            raise RuntimeError('Worker cannot modify config; see mutation.json')
        save(folder / 'ready.json', {'flags': get_limits(api, job).basic.flags,
                                     'active': api.active(job), 'manager_sole_job_holder': True})
        time.sleep(50)
    finally:
        # This is bypassed by the controller's TerminateProcess simulation.
        if job:
            api.k.TerminateJobObject(job, 1)
        if info:
            api.close(info.thread)
            api.close(info.process)
        api.close(job)


def observe(folder):
    api = API()
    intent = read(folder / 'intent.json')
    states = {}
    for role in ('root', 'leaf'):
        identity = read(folder / (role + '.json'))
        handle = api.process(identity['pid'])
        try:
            if api.birth(handle) != identity['birth']:
                raise RuntimeError('Process identity changed; refusing inference')
            value = api.k.WaitForSingleObject(handle, 0)
            if value not in (0, 258):
                raise C.WinError(C.get_last_error())
            states[role] = 'exited' if value == 0 else 'alive'
        finally:
            api.close(handle)
    return {'processes': states, 'open': api.opened(intent['job']),
            'production_release_authorized': False}


def fresh_observer(folder):
    child = subprocess.run(command('--observe', str(folder)), capture_output=True, text=True,
                           encoding='utf-8', timeout=10, close_fds=True, creationflags=0x08000000)
    if child.returncode:
        raise RuntimeError(child.stderr)
    return json.loads(child.stdout)


def run(output):
    api = API()
    rows, held, proc = [], [], None
    intent = {'attempt': uuid.uuid4().hex, 'job': 'Local\\Task6Focused_' + uuid.uuid4().hex,
              'mode': MODE}
    save(output / 'intent.json', intent)

    def emit(case, status, **details):
        rows.append(dict(case=case, status=status, error=details.pop('error', None), **details))
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
        save(output / 'report.json', {'results': rows, 'production_verified': False})

    try:
        with (output / 'manager.log').open('wb') as log:
            proc = subprocess.Popen(command('--manager', str(output)), stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=log, close_fds=True, creationflags=0x08000000)
            until(lambda: (output / 'ready.json').exists() or proc.poll() is not None)
            if not (output / 'ready.json').exists():
                mutation = read(output / 'mutation.json') if (output / 'mutation.json').exists() else None
                emit('setup', 'UNKNOWN', mutation=mutation, reason='No completed mutation; inspect manager.log')
            else:
                created = read(output / 'created.json')
                for role in ('root', 'leaf'):
                    identity = read(output / (role + '.json'))
                    handle = api.process(identity['pid'])
                    if api.birth(handle) != identity['birth']:
                        api.close(handle)
                        raise RuntimeError('PID reuse before crash; do not terminate by bare PID')
                    held.append((role, handle))  # PROCESS handles only, for independent cleanup
                leaf = read(output / 'leaf.json')
                mutation = read(output / 'mutation.json')
                expected_flags = not (mutation['actual_flags'] & 0x2000) if MODE == 'lifecycle' else bool(mutation['actual_flags'] & 0x800)
                emit('worker_configuration_change', 'PASS' if expected_flags else 'FAIL',
                     observation=mutation, error=mutation['error'])
                expected_member = MODE == 'lifecycle'
                emit('leaf_membership', 'PASS' if leaf['in_target_job'] is expected_member else 'UNKNOWN',
                     observation=leaf, error=leaf['membership_error'],
                     reason='Outer/nested Job policy can prevent breakaway; never fabricate escape')
                if leaf['in_target_job'] is not expected_member or not expected_flags:
                    emit('scenario', 'UNKNOWN', reason='Required setup not established; no crash inference')
                else:
                    proc.kill()
                    proc.wait(timeout=8)
                    # Observer is a new process. Controller holds no Job handle.
                    observation = fresh_observer(output)
                    if MODE == 'lifecycle':
                        alive = all(v == 'alive' for v in observation['processes'].values())
                        emit('last_job_handle_closed_workers_alive', 'PASS' if alive else 'FAIL', observation=observation,
                             error=observation['open']['error'])
                        emit('live_job_name_reopen', 'PASS' if alive and observation['open']['opened'] else 'FAIL',
                             observation=observation, error=observation['open']['error'],
                             expectation='Job still queryable while associated workers alive; contrary result retained as FAIL')
                    else:
                        deadline = time.monotonic() + 8
                        while (observation['processes']['root'] != 'exited' or observation['open']['opened']) and time.monotonic() < deadline:
                            time.sleep(0.1)
                            observation = fresh_observer(output)
                        counterexample = (observation['processes'] == {'root': 'exited', 'leaf': 'alive'}
                                          and not observation['open']['opened'] and observation['open']['error'] == 2)
                        emit('breakaway_counterexample', 'PASS' if counterexample else 'UNKNOWN',
                             observation=observation, error=observation['open']['error'])
                        if counterexample:
                            emit('naive_absence_rule_safety', 'FAIL', error=2,
                                 reason='Created record + missing original Job would permit while known escaped leaf remains alive.')
                        # Audit evidence exists ONLY in this experiment, not automatically in production.
                        guarded = 'BLOCK_KNOWN_ESCAPE' if leaf['in_target_job'] is False else 'UNKNOWN'
                        emit('guard_with_explicit_experiment_escape_evidence', 'PASS' if guarded == 'BLOCK_KNOWN_ESCAPE' else 'UNKNOWN',
                             decision=guarded, kind='experimental_guard_NOT_production_fix',
                             reason='Without trustworthy history/containment enforcement production must remain UNKNOWN; initial flags alone do not certify history.')
    except Exception as error:
        emit('experiment_error', 'FAIL', detail=str(error), error=getattr(error, 'winerror', None))
    finally:
        cleanup_ok = True
        # Terminate manager first to prevent it resuming new work while cleaning nodes.
        if proc and proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cleanup_ok = False
        # Exact recorded process objects, leaf first. No taskkill, no PID enumeration.
        for role, handle in reversed(held):
            try:
                wait = api.k.WaitForSingleObject(handle, 0)
                if wait == 258:
                    api.check(api.k.TerminateProcess(handle, 1))
                cleanup_ok = cleanup_ok and api.k.WaitForSingleObject(handle, 5000) == 0
            except OSError:
                cleanup_ok = False
            finally:
                api.close(handle)
        # If setup failed before exact handles were acquired, workers self-expire.
        emit('cleanup', 'PASS' if cleanup_ok and len(held) == 2 else 'UNKNOWN',
             exact_process_handles=len(held), reason='Incomplete setup relies additionally on bounded worker watchdogs')
    if len(held) == 2:
        try:
            deadline = time.monotonic() + 5
            observed = api.opened(intent['job'])
            while observed['opened'] and time.monotonic() < deadline:
                time.sleep(0.1)
                observed = api.opened(intent['job'])
            emit('after_cleanup_name_absent', 'PASS' if not observed['opened'] and observed['error'] == 2 else 'UNKNOWN',
                 observation=observed, error=observed['error'])
        except OSError as error:
            emit('after_cleanup_name_absent', 'UNKNOWN', detail=str(error))
    code = 1 if any(r['status'] == 'FAIL' for r in rows) else (2 if any(r['status'] == 'UNKNOWN' for r in rows) else 0)
    save(output / 'report.json', {'mode': MODE, 'results': rows, 'exit_code': code,
                                'production_verified': False, 'python': sys.version,
                                'windows': str(sys.getwindowsversion())})
    print('RESULT exit_code=%d production_verified=false report=%s' % (code, output / 'report.json'), flush=True)
    return code


def main():
    if os.name != 'nt' or sys.getwindowsversion().major < 10:
        print('UNKNOWN Windows 10+ required')
        return 2
    parser = argparse.ArgumentParser(description='Focused temporary Job experiment; never production recovery')
    parser.add_argument('--manager', help=argparse.SUPPRESS)
    parser.add_argument('--node', nargs=2, help=argparse.SUPPRESS)
    parser.add_argument('--observe', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.manager:
        manager(Path(args.manager))
        return 0
    if args.node:
        node(Path(args.node[0]), args.node[1])
        return 0
    if args.observe:
        print(json.dumps(observe(Path(args.observe))))
        return 0
    output = Path(tempfile.mkdtemp(prefix='task6-' + MODE + '-'))
    print('Evidence directory: ' + str(output), flush=True)
    return run(output)


if __name__ == '__main__':
    raise SystemExit(main())
