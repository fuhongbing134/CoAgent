"""Isolated HTTP/SQLite benchmark. Never starts a model CLI.

run creates a NEW evidence directory; report only reads those JSONL files.
Each case runs in a fresh child process with an exclusively created database.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import platform
import random
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from unittest.mock import patch

REPO = Path(__file__).resolve().parent


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')


def checked_root(value, *, create=False):
    raw = Path(value).absolute()
    for part in [raw, *raw.parents]:
        if part.exists() and (part.is_symlink() or part.is_junction()):
            raise ValueError('Linked evidence path forbidden')
    root = raw.resolve()
    if not root.is_relative_to(REPO) or root == REPO:
        raise ValueError('Evidence must be in a new subdirectory of the repository')
    if create:
        root.mkdir(parents=True, exist_ok=False)
    return root


def quantile(values, p):
    if not 0 < p <= 1:
        raise ValueError('p must be in (0, 1]')
    return sorted(values)[math.ceil(p * len(values)) - 1] if values else None


def report(root):
    result = []
    for path in sorted(root.glob('case-*/samples.jsonl')):
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        measured = [r for r in rows if not r['warmup']]
        success = [r for r in measured if r['status'] == 'completed']
        latency = [(r['t_observed'] - r['t_send']) / 1e6 for r in success]
        start = min((r['t_send'] for r in measured), default=None)
        end = max((r['t_observed'] for r in measured), default=None)
        seconds = (end - start) / 1e9 if start is not None and end > start else None
        result.append(dict(case=path.parent.name, n=len(measured), successful=len(success),
                           failed=sum(r['status'] == 'failed' for r in measured),
                           censored=sum(r['censored'] for r in measured),
                           http_statuses={str(s): sum(r['http_status'] == s for r in measured)
                                          for s in sorted({r['http_status'] for r in measured})},
                           p50_ms=quantile(latency, .50), p95_ms=quantile(latency, .95),
                           p99_ms=quantile(latency, .99), max_ms=max(latency, default=None),
                           cohort_seconds=seconds,
                           cohort_success_per_second=len(success)/seconds if seconds else None,
                           raw_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        result[-1]['config'] = json.loads((path.parent/'config.json').read_text(encoding='utf-8')) if (path.parent/'config.json').exists() else None
        events_path = path.parent/'events.jsonl'
        if events_path.exists():
            live, peak = 0, 0
            for event in sorted((json.loads(line) for line in events_path.read_text(encoding='utf-8').splitlines()), key=lambda e:e['t']):
                if event['kind'] == 'worker_enter':
                    live += 1
                    peak = max(peak, live)
                elif event['kind'] == 'worker_exit':
                    live -= 1
            result[-1]['peak_exec_from_events'] = peak
            result[-1]['unclosed_worker_intervals'] = live
    dump(root / 'summary.json', result)
    return result


def child(root, config):
    # Set before importing any application module; never inspect the runtime DB.
    dbpath = root / 'isolated.db'
    fd = os.open(dbpath, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(fd)
    if dbpath.stat().st_nlink != 1:
        raise ValueError('Database hard link forbidden')
    os.environ['AGENT_BRIDGE_DB'] = str(dbpath)
    import web_chat as web
    from execution_tracking import ExecutionTracker, DomainBusy
    from managed_process import ProcessResult, ManagedProcess
    from test_direct_answer import answer, envelope
    assert web.DB_PATH.resolve() == dbpath.resolve()
    events, samples = [], []
    guard = threading.Lock()
    gate = threading.Event()
    entered = threading.Condition()
    live = set()
    peak = 0
    hold = config['hold_ms'] / 1000
    tracker = ExecutionTracker(dbpath)
    begin_wait = threading.Event()
    original_connect = web.connect_workbench

    def observed_connect(*args, **kwargs):
        db = original_connect(*args, **kwargs)
        db.set_trace_callback(lambda sql: begin_wait.set() if sql.strip().upper() == 'BEGIN IMMEDIATE' else None)
        return db

    def event(kind, **fields):
        with guard:
            events.append(dict(t=time.monotonic_ns(), kind=kind, **fields))

    def query(sql, args=()):
        with closing(sqlite3.connect(dbpath, timeout=10)) as db:
            return db.execute(sql, args).fetchall()

    def fixture_call(job, conversation):
        with closing(sqlite3.connect(dbpath)) as db, db:
            db.execute("INSERT INTO collab_jobs(job_id,request,plan) VALUES (?,?,?)", (job,'benchmark','{}'))
            db.execute("INSERT INTO collab_lifecycle(job_id) VALUES (?)", (job,))
            db.execute("INSERT INTO collab_bindings VALUES (?,?)", (job, conversation))
            db.execute("INSERT INTO collab_calls(job_id,call_id,agent_id,role) VALUES (?,'call','codex','collaborator')", (job,))

    def invoke(job):
        return web.invoke_collaboration(job, 'codex', 'collaborator', 'benchmark', call_id='call')

    def worker(conversation, prompt, message_id, user_message_id):
        nonlocal peak
        execution = web.active_run(message_id)['execution']
        # Exercise the production default session selection, not a new resolver.
        agent = web.active_run(message_id)['agent_id']
        web.prepare_execution_session(message_id, agent, 'SCRIPTED', {})
        tracker.mark_running(execution.token)
        event('worker_enter', message_id=message_id, conversation=conversation)
        with entered:
            live.add(message_id)
            peak = max(peak, len(live))
            entered.notify_all()
        try:
            if config['mode'] == 'correctness':
                if not gate.wait(10):
                    raise TimeoutError('Controller did not release worker')
            else:
                time.sleep(hold)
            execution.result = ProcessResult('completed', 0, b'', b'', 'confirmed')
            text = envelope(answer()) if web.route_message(prompt)[1] else str(message_id)
            return web.worker_reply(message_id, text, 'completed')
        finally:
            event('worker_exit', message_id=message_id, conversation=conversation)
            with entered:
                live.remove(message_id)
                entered.notify_all()

    def send(server, conversation, request_id, warmup=False, recipients=None):
        begin = time.monotonic_ns()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=30)
        try:
            connection.request('POST', '/api/messages', json.dumps(dict(
                conversation_id=conversation, content='benchmark', recipients=recipients or ['codex'])),
                {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{server.server_port}'})
            response = connection.getresponse()
            data = json.loads(response.read())
            status = response.status
        finally:
            connection.close()
        received = time.monotonic_ns()
        row = dict(schema_version=1, request_id=request_id, conversation=conversation,
                   agent='codex', warmup=warmup, t_send=begin, t_response=received,
                   http_status=status, status='rejected', censored=False,
                   t_observed=received, message_id=None, reason=data.get('error'))
        if status == 202:
            mids = list(data['assistant_message_ids'].values())
            row['message_id'] = mids
            while time.monotonic_ns() - begin < 30_000_000_000:
                states = [query('SELECT status FROM workbench_messages WHERE id=?', (mid,))[0][0] for mid in mids]
                # Wait for release as well, avoiding ACTIVE_RUNS busy races on next iteration.
                if all(state != 'pending' for state in states) and all(web.active_run(mid) is None for mid in mids):
                    row['status'] = 'completed' if all(s == 'completed' for s in states) else 'failed'
                    break
                time.sleep(.02)
            else:
                row.update(status='timeout', censored=True)
            row['t_observed'] = time.monotonic_ns()
        with guard:
            samples.append(row)
        return row

    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(web, 'DB_PATH', dbpath))
            stack.enter_context(patch.object(web, 'agent_worker', return_value=worker))
            stack.enter_context(patch.object(web, 'connect_workbench', side_effect=observed_connect))
            stack.enter_context(patch.object(web, 'resolve_codex_command', return_value=['SCRIPTED']))
            stack.enter_context(patch.object(web, 'resolve_dsh_command', return_value=['SCRIPTED']))
            stack.enter_context(patch.object(web, 'resolve_grok_command', return_value=['SCRIPTED']))
            stack.enter_context(patch.object(web.ChatHandler, 'log_message', lambda *a: None))
            stack.enter_context(patch.object(ManagedProcess, 'run', side_effect=AssertionError('Real process forbidden')))
            server = stack.enter_context(web.startup_server(0))
            serving = threading.Thread(target=server.serve_forever, daemon=True)
            serving.start()
            try:
                with closing(sqlite3.connect(dbpath)) as db, db:
                    db.execute("INSERT INTO agents VALUES ('deepseek-harness','fake','fake','fake','fake','','')")
                    for i in range(100):
                        db.execute('INSERT INTO workbench_sessions VALUES (?,?,?)',
                                   (f'room-{i}', f'fake-session-{i}', 'benchmark'))
                # Legal terminal fixtures in one transaction; seed cost is not benchmark latency.
                with closing(sqlite3.connect(dbpath)) as db, db:
                    db.executemany('''INSERT INTO execution_runs
                        (run_id,agent_id,conversation_id,source_kind,instance_id,config_snapshot,state)
                        VALUES (?,'codex',?,'workbench','seed','{}','failed')''',
                        ((f'seed-{i}', f'room-{i % 100}') for i in range(config['history'])))
                event('seed_done', history=config['history'])
                if config['mode'] == 'correctness':
                    # C17: second actual server process must lose the same DB lock.
                    conflict = subprocess.run([sys.executable, '-c',
                        'import web_chat as w;\nwith w.startup_server(0): pass'],
                        capture_output=True, text=True, encoding='utf-8', timeout=15,
                        env={**os.environ, 'PYTHONIOENCODING':'utf-8'})
                    assert conflict.returncode != 0 and 'execution-service lock' in conflict.stderr
                    event('second_instance_rejected', exit_code=conflict.returncode)
                    with ThreadPoolExecutor(3) as pool:
                        first = pool.submit(send, server, 'room-0', 'first')
                        with entered:
                            assert entered.wait_for(lambda: len(live) == 1, timeout=10), 'No worker entered'
                        # C1: while the winner is held, same-domain POST must reject.
                        busy = send(server, 'room-0', 'busy')
                        assert busy['http_status'] == 409, busy
                        # C8: a collaboration call must not steal the HTTP owner's domain.
                        before = query("SELECT owner_run_id,fence FROM execution_domains WHERE conversation_id='room-0'")
                        fixture_call('same-domain', 'room-0')
                        assert invoke('same-domain').state == 'failed'
                        assert query("SELECT owner_run_id,fence FROM execution_domains WHERE conversation_id='room-0'") == before
                        second = pool.submit(send, server, 'room-1', 'second')
                        with entered:
                            assert entered.wait_for(lambda: len(live) == 2, timeout=10), 'No cross-domain overlap'
                        domains = query("SELECT count(*) FROM execution_domains WHERE state='running'")[0][0]
                        assert domains == 2, domains
                        event('overlap_observed', domains=domains, workers=2)
                        gate.set()
                        assert first.result()['status'] == second.result()['status'] == 'completed'
                    # C13: claim first and second domains, then visible legacy conflict.
                    with closing(sqlite3.connect(dbpath)) as db, db:
                        db.execute("UPDATE workbench_sessions SET codex_thread_id='shared' WHERE conversation_id IN ('room-0','room-1')")
                    a = tracker.begin_run(agent_id='codex', conversation_id='room-0', instance_id='c13', source_kind='workbench', config_snapshot={})
                    tracker.select_legacy_session(a, 'codex')
                    b = tracker.begin_run(agent_id='codex', conversation_id='room-1', instance_id='c13', source_kind='workbench', config_snapshot={})
                    try:
                        tracker.select_legacy_session(b, 'codex')
                    except DomainBusy as error:
                        event('session_conflict', reason=error.reason_code)
                    else:
                        raise AssertionError('Shared session accepted')
                    tracker.finish_run(b, outcome='failed', exit_evidence='not_started')
                    assert query("SELECT state FROM execution_domains WHERE conversation_id='room-1'")[0][0] == 'idle'
                    assert query("SELECT owner_run_id,fence FROM execution_domains WHERE conversation_id='room-0'")[0] == (a.run_id, a.fence)
                    tracker.finish_run(a, outcome='failed', exit_evidence='not_started')
                    # C4/C5: one real HTTP broadcast, all three workers held together.
                    gate.clear()
                    with ThreadPoolExecutor(1) as pool:
                        broadcast = pool.submit(send, server, 'broadcast', 'broadcast', False,
                                                ['codex','deepseek-harness','grok'])
                        with entered:
                            overlap = entered.wait_for(lambda: len(live) == 3, timeout=10)
                        gate.set()
                        assert overlap, 'Broadcast did not overlap all workers'
                        assert broadcast.result()['status'] == 'completed'
                    event('broadcast_overlap_observed', workers=3)
                    # C7: observe the original lock, preserving acquire/release semantics.
                    waiting = threading.Event()
                    original_lock = web.COLLAB_AGENT_LOCKS['codex']
                    class ObservedLock:
                        def __enter__(self):
                            event('lock_wait', thread=threading.get_ident())
                            waiting.set()
                            original_lock.acquire()
                            event('lock_acquired', thread=threading.get_ident())
                        def __exit__(self, *args):
                            event('lock_release', thread=threading.get_ident())
                            original_lock.release()
                    fixture_call('queue-a', 'queue-a')
                    fixture_call('queue-b', 'queue-b')
                    gate.clear()
                    with patch.dict(web.COLLAB_AGENT_LOCKS, codex=ObservedLock()), ThreadPoolExecutor(2) as pool:
                        queued_a = pool.submit(invoke, 'queue-a')
                        with entered:
                            first_entered = entered.wait_for(lambda: len(live)==1, timeout=10)
                        waiting.clear()
                        queued_b = pool.submit(invoke, 'queue-b')
                        second_waiting = waiting.wait(10)
                        with entered:
                            only_first = len(live)==1
                        gate.set()
                        assert first_entered and second_waiting and only_first
                        assert queued_a.result().state == queued_b.result().state == 'completed'
                    queue_events = [e for e in events if e.get('conversation') in {'queue-a','queue-b'}]
                    a_end = next(e['t'] for e in queue_events if e['kind']=='worker_exit' and e['conversation']=='queue-a')
                    b_start = next(e['t'] for e in queue_events if e['kind']=='worker_enter' and e['conversation']=='queue-b')
                    assert b_start >= a_end
                    event('queue_serialization_verified')
                    # C9: bridge functions and HTTP operate on the same isolated WAL DB.
                    import agent_bridge as bridge
                    bridge.AGENT_ID = 'bench-bridge'  # Fixed identity, never mutated by threads.
                    bridge.register_agent('bench', 'script', 'none', 'test')
                    with closing(sqlite3.connect(dbpath)) as db, db:
                        db.execute("INSERT INTO agents VALUES ('codex','fake','fake','fake','fake','','')")
                    def bridge_writes():
                        ids = [bridge.send_message('codex', f'bridge-{i}', 'mixed')['message_id'] for i in range(20)]
                        return ids
                    with ThreadPoolExecutor(2) as pool:
                        writes = pool.submit(bridge_writes)
                        response = pool.submit(send, server, 'mixed', 'mixed-web')
                        ids = writes.result()
                        assert response.result()['status'] == 'completed'
                    assert len(set(ids)) == 20
                    assert query("SELECT content FROM messages WHERE conversation_id='mixed' ORDER BY id") == [(f'bridge-{i}',) for i in range(20)]
                    event('mixed_writes_reconciled', count=20)
                    # C15: production timeout unchanged; handshake is the acquired write transaction.
                    for duration, expected in [(.1, 202), (15, 503)]:
                        with closing(sqlite3.connect(dbpath, timeout=10)) as blocker:
                            blocker.execute('BEGIN IMMEDIATE')
                            begin_wait.clear()
                            with ThreadPoolExecutor(1) as pool:
                                future = pool.submit(send, server, 'lock', f'lock-{duration}')
                                # Allow preflight to finish; never infer DB wait from client send time.
                                reached_begin = begin_wait.wait(15)
                                if not reached_begin:
                                    blocker.rollback()
                                    raise RuntimeError('invalid: HTTP did not reach BEGIN IMMEDIATE under hold')
                                if expected == 503:
                                    try:
                                        locked = future.result(timeout=20)
                                    finally:
                                        blocker.rollback()
                                else:
                                    time.sleep(duration)
                                    blocker.rollback()
                                    locked = future.result()
                        assert locked['http_status'] == expected, locked
                        event('lock_outcome', hold_seconds=duration, http_status=expected)
                else:
                    def client(client_id, count, warmup):
                        for i in range(count):
                            row = send(server, f'room-{client_id}', f'{warmup}-{client_id}-{i}', warmup)
                            assert row['status'] == 'completed', row
                    for warmup, total in [(True, config['warmup']), (False, config['samples'])]:
                        with ThreadPoolExecutor(config['clients']) as pool:
                            futures = [pool.submit(client, i, total // config['clients'] + (i < total % config['clients']), warmup)
                                       for i in range(config['clients'])]
                            for future in futures:
                                future.result()
                assert query('PRAGMA integrity_check') == [('ok',)]
                assert query('PRAGMA foreign_key_check') == []
                assert query("SELECT count(*) FROM execution_domains WHERE state!='idle'")[0][0] == 0
                dump(root/'result.json', dict(status='passed', layer='L2-scripted', peak_exec=peak,
                     journal_mode=query('PRAGMA journal_mode')[0][0], config=config,
                     final_runs=query('SELECT count(*) FROM execution_runs')[0][0]))
            finally:
                gate.set()
                server.shutdown()
                serving.join(10)
                assert not serving.is_alive()
        if config['mode'] == 'correctness':
            # C14: a real new process initializes the same isolated database after shutdown.
            token = tracker.begin_run(agent_id='codex', conversation_id='restart', instance_id='old',
                                      source_kind='workbench', config_snapshot={})
            with closing(sqlite3.connect(dbpath)) as db, db:
                db.execute("INSERT INTO workbench_messages(conversation_id,sender_id,recipient_id,content,created_at,status) VALUES ('restart','codex','user','','','pending')")
            restarted = subprocess.run([sys.executable, '-c',
                'import web_chat as w;\nwith w.startup_server(0): pass'],
                capture_output=True, text=True, encoding='utf-8', timeout=20,
                env={**os.environ, 'PYTHONIOENCODING':'utf-8'})
            assert restarted.returncode == 0, restarted.stderr
            assert query("SELECT state,owner_run_id FROM execution_domains WHERE conversation_id='restart'") == [('reserved',token.run_id)]
            assert query("SELECT status FROM workbench_messages WHERE conversation_id='restart'") == [('cancelled',)]
            event('restart_retained_occupancy', owner_run_id=token.run_id)
            # Deliberately retain this unknown old occupancy as evidence; no forced clearing.
    except BaseException:
        dump(root/'result.json', dict(status='failed', config=config, traceback=traceback.format_exc()))
        raise
    finally:
        for name, rows in [('samples', samples), ('events', events)]:
            (root/f'{name}.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows), encoding='utf-8')


def run(args):
    root = checked_root(args.output, create=True)
    def git(*parts):
        return subprocess.check_output(['git', '-c', f'safe.directory={REPO}', *parts], cwd=REPO).decode().strip()
    dump(root/'manifest.json', dict(layer='L2-scripted', python=sys.version, sqlite=sqlite3.sqlite_version,
         os=platform.platform(), cpu=platform.processor(), commit=git('rev-parse', 'HEAD'),
         branch=git('branch', '--show-current'), dirty_diff_sha256=hashlib.sha256(git('diff').encode()).hexdigest(),
         tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), seed=3049,
         real_cli_calls=0, l3_directory='l3-process', l4_directory='l4-cli',
         missing_metrics=['lock wait (ordinary path has no collaboration lock)', 'terminal commit timestamp', 'resource sampling'],
         argv=sys.argv))
    started = time.monotonic()
    configs = [dict(mode='correctness', clients=2, history=h, hold_ms=0, warmup=0, samples=0) for h in [0,10000]]
    if not args.correctness_only:
        loads = [dict(mode='load', clients=n, history=h, hold_ms=hold, warmup=args.warmup, samples=args.samples, round=r)
                 for n in [1,2,4,8] for h in [0,1000,10000] for hold in [50] for r in range(args.rounds)]
        random.Random(3049).shuffle(loads)
        configs += loads
    for i, config in enumerate(configs):
        if time.monotonic() - started > 7200:
            dump(root/'stopped.json', dict(reason='two_hour_budget', remaining=len(configs)-i))
            break
        case = root/f'case-{i:03}'
        case.mkdir()
        dump(case/'config.json', config)
        dump(case/'version.json', dict(tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
        with (case/'execution.log').open('w', encoding='utf-8') as log:
            done = subprocess.run([sys.executable, str(Path(__file__).resolve()), '_child', '--output', str(case)],
                                  stdout=log, stderr=subprocess.STDOUT, timeout=900,
                                  env={**os.environ, 'PYTHONIOENCODING':'utf-8', 'AGENT_BRIDGE_DB':str(case/'isolated.db')})
        print(case.name, config, 'exit', done.returncode, flush=True)
        if done.returncode:
            report(root)
            raise SystemExit(done.returncode)  # Stop immediately, retain first failure.
    report(root)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['run','report','_child'])
    parser.add_argument('--output', required=True)
    parser.add_argument('--samples', type=int, default=1000)
    parser.add_argument('--warmup', type=int, default=100)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--correctness-only', action='store_true')
    args = parser.parse_args()
    if min(args.samples, args.rounds) < 1 or args.warmup < 0:
        parser.error('Positive samples/rounds and nonnegative warmup required')
    if args.command == 'run':
        run(args)
    elif args.command == 'report':
        print(json.dumps(report(checked_root(args.output)), indent=2))
    else:
        child(checked_root(args.output), json.loads((Path(args.output)/'config.json').read_text(encoding='utf-8')))
