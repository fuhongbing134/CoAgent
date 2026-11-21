"""Standalone task6 prerequisite probe; authoring copy of ctypes declarations. Run manually; no production imports.

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
        return self.check(self.k.OpenProcess(0x100000 | 0x1000, False, pid))

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
            cmd = command('--worker', str(folder), '0', mode)
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


def context(api):
    sid = W.DWORD()
    api.check(api.k.ProcessIdToSessionId(os.getpid(), C.byref(sid)))
    before = time.time_ns() // 1000000
    ticks = api.k.GetTickCount64()
    after = time.time_ns() // 1000000
    return {'session_id': sid.value, 'uptime_ms': ticks,
            'estimated_boot_ms': (before + after) // 2 - ticks,
            'sampling_span_ms': after - before,
            'source': 'wall_clock_minus_GetTickCount64; NOT trusted boot identity',
            'production_guard_verified': False}


def limits(api, job):
    value = ExtendedLimits()
    api.check(api.k.QueryInformationJobObject(job, 9, C.byref(value), C.sizeof(value), None))
    return value


def open_job(api, name, access):
    C.set_last_error(0)
    handle = api.k.OpenJobObjectW(access, False, name)
    return handle, (0 if handle else C.get_last_error())


def worker(folder, depth, mode):
    api = API()
    record = read(folder / 'intent.json')
    # No controller or recovery process obtains a node process handle.
    inside = W.BOOL()
    current = api.k.OpenProcess(0x1000, False, os.getpid())
    try:
        api.check(current)
        api.check(api.k.IsProcessInJob(current, None, C.byref(inside)))
    finally:
        api.close(current)
    save(folder / ('node%d.json' % depth), {'pid': os.getpid(), 'in_some_job': bool(inside.value)})
    if mode == 'mutation':
        handle, error = open_job(api, record['job'], 2 | 4)
        result = {'open_error': error, 'opened_for_set': bool(handle), 'changed': False,
                  'restore_ok': None, 'in_some_job': bool(inside.value)}
        original = None
        try:
            if handle:
                original = limits(api, handle)
                changed = ExtendedLimits.from_buffer_copy(original)
                changed.basic.flags &= ~0x2000  # only this test Job; no breakaway enabled
                C.set_last_error(0)
                ok = api.k.SetInformationJobObject(handle, 9, C.byref(changed), C.sizeof(changed))
                result['set_error'] = 0 if ok else C.get_last_error()
                result['changed'] = bool(ok and not (limits(api, handle).basic.flags & 0x2000))
        except OSError as error:
            result.update(detail=str(error), probe_error=getattr(error, 'winerror', None))
        finally:
            if original is not None:
                result['restore_ok'] = bool(api.k.SetInformationJobObject(handle, 9, C.byref(original), C.sizeof(original)))
            api.close(handle)
        # WRITE_DAC is observed, never exercised. A SET denial alone is not immutability.
        dac, dac_error = open_job(api, record['job'], 0x40000)
        result.update(write_dac_available=bool(dac), write_dac_error=dac_error)
        api.close(dac)
        save(folder / 'mutation.json', result)
        return
    child = None
    try:
        if depth < 2:
            child = subprocess.Popen(command('--worker', str(folder), str(depth + 1), mode),
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, close_fds=True, creationflags=0x08000000)
        if mode == 'root_exit' and depth == 0:
            until(lambda: (folder / 'node2.json').exists())
            return  # ordinary descendants deliberately outlive the root
        time.sleep(35)
    finally:
        # root_exit must leave descendants alive for its negative control.
        if child and not (mode == 'root_exit' and depth == 0) and child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def manager(folder, mode):
    api = API()
    record = read(folder / 'intent.json')
    job, info = None, None
    try:
        job = api.create(record['job'])
        info = api.launch(job, folder, mode)
        actual = limits(api, job).basic.flags
        record.update(created=True, flags=actual, root_pid=info.pid, membership_verified=True,
                      context=context(api), attempt=record['attempt'])
        save(folder / 'created.json', record)
        if api.k.ResumeThread(info.thread) == 0xFFFFFFFF:
            raise C.WinError(C.get_last_error())
        if mode == 'mutation':
            until(lambda: (folder / 'mutation.json').exists())
        else:
            until(lambda: all((folder / ('node%d.json' % d)).exists() for d in range(3)))
        if mode == 'root_exit':
            until(lambda: api.k.WaitForSingleObject(info.process, 0) == 0)
        save(folder / 'ready.json', {'active': api.active(job), 'mode': mode,
                                     'root_exited': api.k.WaitForSingleObject(info.process, 0) == 0})
        until(lambda: (folder / 'stop.json').exists(), seconds=50)
    except TimeoutError:
        pass  # watchdog cleanup; not an experiment success
    finally:
        if job:
            # Explicit cleanup even if mutation removed kill-on-close and failed restore.
            api.k.TerminateJobObject(job, 1)
        if info:
            api.close(info.thread)
            api.close(info.process)
        api.close(job)


def decision(record, intent, now, observation, *, identity_conflict=False):
    if identity_conflict:
        return 'BLOCK_IDENTITY_CONFLICT'
    if not record or not record.get('created') or not record.get('membership_verified'):
        return 'BLOCK_NO_CREATED'
    if any(record.get(key) != intent.get(key) for key in ('attempt', 'job')):
        return 'BLOCK_BINDING'
    flags = record.get('flags', 0)
    if not flags & 0x2000 or flags & (0x800 | 0x1000):
        return 'BLOCK_CONFIG'
    old = record.get('context', {})
    if old.get('session_id') != now.get('session_id'):
        return 'BLOCK_SESSION'
    if abs(old.get('estimated_boot_ms', 0) - now.get('estimated_boot_ms', 0)) > 1000:
        return 'BLOCK_BOOT_OBSERVATION'
    if observation['opened']:
        return 'BLOCK_JOB_EXISTS'
    if observation['error'] == 5:
        return 'BLOCK_ACCESS_DENIED'
    if observation['error'] != 2:
        return 'BLOCK_OTHER_ERROR'
    return 'EXPERIMENTAL_CANDIDATE_ONLY'


def recover(folder):
    api = API()
    intent = read(folder / 'intent.json')
    created = read(folder / 'created.json') if (folder / 'created.json').exists() else None
    now = context(api)
    observed = api.opened(intent['job'])
    return {'decision': decision(created, intent, now, observed), 'open': observed,
            'context': now, 'enumerated_nodes': False, 'preheld_node_or_job_handles': False,
            'production_release_authorized': False,
            'reason': 'Boot estimate and default-token Job immutability are not production proof.'}


def run(output):
    api = API()
    rows = []

    def emit(case, status, **details):
        row = dict(case=case, status=status, error=details.pop('error', None), **details)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        save(output / 'report.json', {'results': rows, 'production_verified': False})

    samples = []
    for _ in range(3):
        samples.append(context(api))
        time.sleep(0.1)
    stable = len({v['session_id'] for v in samples}) == 1
    spread = max(v['estimated_boot_ms'] for v in samples) - min(v['estimated_boot_ms'] for v in samples)
    emit('context_short_term_observation', 'PASS' if stable and spread < 1000 else 'UNKNOWN',
         samples=samples, estimated_boot_spread_ms=spread)
    emit('context_production_reliability', 'UNKNOWN',
         reason='No reboot/relogin performed. Wall clock can change; session IDs are not unique logon/boot identities. Stored JSON is editable. Stable estimates do not prove continuity.')
    emit('boot_estimate_clock_shift_counterexample', 'PASS', kind='model_not_system_clock_change',
         reason='wall_clock_ms minus uptime_ms shifts by the same amount as wall clock; no system clock modified',
         original=samples[0]['estimated_boot_ms'], shifted=samples[0]['estimated_boot_ms'] + 3600000)

    for mode in ('plain', 'held', 'root_exit', 'mutation'):
        folder = output / mode
        folder.mkdir()
        intent = {'attempt': uuid.uuid4().hex, 'job': 'Local\\Task6Prereq_' + uuid.uuid4().hex}
        save(folder / 'intent.json', intent)
        proc, holder = None, None
        try:
            with (folder / 'manager.log').open('wb') as log:
                proc = subprocess.Popen(command('--manager', str(folder), mode), stdin=subprocess.DEVNULL,
                                        stdout=log, stderr=log, close_fds=True, creationflags=0x08000000)
                until(lambda: (folder / 'ready.json').exists() or proc.poll() is not None)
                if not (folder / 'ready.json').exists():
                    raise RuntimeError('Manager failed; inspect manager.log')
                ready = read(folder / 'ready.json')
                if mode == 'mutation':
                    observation = read(folder / 'mutation.json')
                    if observation['changed'] or observation['write_dac_available']:
                        status = 'FAIL'
                    elif observation['open_error'] == 5:
                        status = 'UNKNOWN'  # one denied operation does not prove all mutation paths blocked
                    else:
                        status = 'UNKNOWN'
                    emit('job_configuration_immutable_default_token', status, observation=observation,
                         reason='FAIL means the intended prerequisite is false, not a probe crash.')
                    save(folder / 'stop.json', {'stop': True})
                    proc.wait(timeout=8)
                    continue
                if mode == 'root_exit':
                    observation = recover(folder)
                    emit('root_exit_descendants_alive', 'PASS' if ready['root_exited'] and ready['active'] > 0
                         and observation['decision'] == 'BLOCK_JOB_EXISTS' else 'FAIL',
                         readiness=ready, observation=observation, error=observation['open']['error'])
                    save(folder / 'stop.json', {'stop': True})
                    proc.wait(timeout=8)
                    continue
                if mode == 'held':
                    # Dedicated negative-control holder, never used in the plain positive test.
                    holder, error = open_job(api, intent['job'], 4 | 8)
                    api.check(holder)
                proc.kill()
                proc.wait(timeout=8)
                child = subprocess.run(command('--recover', str(folder)), capture_output=True, text=True,
                                       encoding='utf-8', timeout=10, close_fds=True, creationflags=0x08000000)
                if child.returncode:
                    raise RuntimeError(child.stderr)
                observation = json.loads(child.stdout)
                if mode == 'held':
                    active = api.active(holder)
                    emit('extra_job_handle_blocks', 'PASS' if active > 0 and observation['decision'] == 'BLOCK_JOB_EXISTS'
                         else 'FAIL', active=active, observation=observation, error=observation['open']['error'])
                else:
                    # Object destruction may settle asynchronously: bounded repeated fresh observers.
                    deadline = time.monotonic() + 8
                    while observation['open']['opened'] and time.monotonic() < deadline:
                        time.sleep(0.1)
                        child = subprocess.run(command('--recover', str(folder)), capture_output=True, text=True,
                                               encoding='utf-8', timeout=10, close_fds=True, creationflags=0x08000000)
                        if child.returncode:
                            raise RuntimeError(child.stderr)
                        observation = json.loads(child.stdout)
                    emit('no_preheld_handles_positive', 'PASS' if observation['decision'] == 'EXPERIMENTAL_CANDIDATE_ONLY'
                         else 'FAIL', observation=observation, error=observation['open']['error'],
                         limitation='Protocol-path observation, not independent enumeration or proof of production preconditions.')
        except Exception as error:
            emit(mode, 'FAIL', detail=str(error), error=getattr(error, 'winerror', None))
        finally:
            if holder:
                api.k.TerminateJobObject(holder, 1)
                api.close(holder)
            if proc and proc.poll() is None:
                save(folder / 'stop.json', {'stop': True})
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)

    # Actual object-name collision: reject before any process is assigned.
    name = 'Local\\Task6Prereq_' + uuid.uuid4().hex
    job = api.create(name)
    try:
        try:
            unexpected = api.create(name)
        except RuntimeError:
            emit('name_collision_rejected', 'PASS', error=183, kind='real_Windows_API')
        else:
            api.close(unexpected)
            emit('name_collision_rejected', 'FAIL')
    finally:
        api.close(job)

    # Actual access error with only the temporary Job's DACL changed.
    sd, denied = C.c_void_p(), None
    try:
        adv = C.WinDLL('advapi32', use_last_error=True)
        convert = adv.ConvertStringSecurityDescriptorToSecurityDescriptorW
        convert.argtypes = [W.LPCWSTR, W.DWORD, C.POINTER(C.c_void_p), C.c_void_p]
        convert.restype = W.BOOL
        api.check(convert('D:(D;;0x0004;;;WD)', 1, C.byref(sd), None))
        security = SecurityAttributes(C.sizeof(SecurityAttributes), sd, False)
        name = 'Local\\Task6Prereq_' + uuid.uuid4().hex
        denied = api.create(name, C.byref(security))
        observed = api.opened(name)
        emit('access_denied_real', 'PASS' if observed == {'opened': False, 'error': 5} else 'UNKNOWN',
             observation=observed, error=observed['error'])
    except OSError as error:
        emit('access_denied_real', 'UNKNOWN', error=getattr(error, 'winerror', None), detail=str(error))
    finally:
        api.close(denied)
        if sd:
            api.k.LocalFree(sd)

    # Deterministic classifier counterexamples, explicitly NOT live PID reuse/logoff.
    intent = {'attempt': 'classifier-fixture', 'job': 'not_opened_by_fixture'}
    now = samples[0]
    base = dict(intent, created=True, membership_verified=True, flags=0x2000, context=now)
    cases = [
        ('missing_created', None, now, {'opened': False, 'error': 2}, False, 'BLOCK_NO_CREATED'),
        ('wrong_session', base, dict(now, session_id=now['session_id'] + 1), {'opened': False, 'error': 2}, False, 'BLOCK_SESSION'),
        ('changed_boot_observation', base, dict(now, estimated_boot_ms=now['estimated_boot_ms'] + 100000), {'opened': False, 'error': 2}, False, 'BLOCK_BOOT_OBSERVATION'),
        ('config_mismatch', dict(base, flags=0), now, {'opened': False, 'error': 2}, False, 'BLOCK_CONFIG'),
        ('pid_reuse_identity_conflict', base, now, {'opened': False, 'error': 2}, True, 'BLOCK_IDENTITY_CONFLICT'),
        ('root_pid_absent_but_job_exists', base, now, {'opened': True, 'error': 0}, False, 'BLOCK_JOB_EXISTS'),
        ('other_open_error', base, now, {'opened': False, 'error': 123}, False, 'BLOCK_OTHER_ERROR'),
        ('access_denied_classifier', base, now, {'opened': False, 'error': 5}, False, 'BLOCK_ACCESS_DENIED'),
    ]
    for label, record, ctx, observed, conflict, expected in cases:
        value = decision(record, intent, ctx, observed, identity_conflict=conflict)
        emit(label, 'PASS' if value == expected else 'FAIL', decision=value, error=observed['error'],
             kind='synthetic_classifier_counterexample_NOT_live_OS_event')
    emit('live_reboot_logoff_pid_reuse', 'UNKNOWN',
         reason='No reboot/logoff, cross-session launch, forced PID reuse, or privileged identity boundary test performed.')
    code = 1 if any(r['status'] == 'FAIL' for r in rows) else (2 if any(r['status'] == 'UNKNOWN' for r in rows) else 0)
    save(output / 'report.json', {'results': rows, 'exit_code': code, 'production_verified': False,
                                'python': sys.version, 'windows': str(sys.getwindowsversion())})
    print('RESULT exit_code=%d production_verified=false report=%s' % (code, output / 'report.json'), flush=True)
    return code


def main():
    if os.name != 'nt' or sys.getwindowsversion().major < 10:
        print('UNKNOWN Windows 10+ required')
        return 2
    parser = argparse.ArgumentParser(description='Standalone task6 prerequisite experiment; never production recovery')
    parser.add_argument('--manager', nargs=2, help=argparse.SUPPRESS)
    parser.add_argument('--worker', nargs=3, help=argparse.SUPPRESS)
    parser.add_argument('--recover', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.manager:
        manager(Path(args.manager[0]), args.manager[1])
        return 0
    if args.worker:
        worker(Path(args.worker[0]), int(args.worker[1]), args.worker[2])
        return 0
    if args.recover:
        print(json.dumps(recover(Path(args.recover))))
        return 0
    output = Path(tempfile.mkdtemp(prefix='task6-prereqs-'))
    print('Evidence directory: ' + str(output), flush=True)
    try:
        return run(output)
    except Exception as error:
        save(output / 'fatal.json', {'error': str(error), 'production_verified': False})
        print('FAIL ' + str(error), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

