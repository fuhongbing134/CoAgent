"""Isolated test-server bootstrap with a controlled Codex backend.

Run only in a dedicated subprocess with a newly created, empty --root.
The parent owns that directory and must retain it until exit is confirmed.
"""
import argparse
import json
import os
from pathlib import Path
import re
import time
import sys
import uuid
import threading


def publish(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    temporary.replace(path)


def diagnostic_excerpt(output):
    excerpt = output[-4096:]
    # Keep only allowlisted traceback tokens, never arbitrary child text.
    lines = re.findall(rb'^  File "[^\r\n]+", line ([0-9]{1,7})(?:,|\r?$)',
                       excerpt, re.MULTILINE)
    exceptions = re.findall(
        rb'^(AssertionError|FileNotFoundError|ImportError|ModuleNotFoundError|OSError|'
        rb'PermissionError|RuntimeError|SyntaxError|TimeoutError|TypeError|'
        rb'UnicodeDecodeError|UnicodeEncodeError|ValueError)(?=:|\r?$)',
        excerpt, re.MULTILINE)
    return {'frame_lines': [int(line) for line in lines[-4:]],
            'exceptions': [name.decode('ascii') for name in exceptions[-2:]],
            'bytes': len(output), 'truncated': len(output) > len(excerpt)}


def install_controlled_backend(web, root):
    from managed_process import ManagedProcess

    marker = str(root / 'controlled-codex')
    script = str(Path(__file__).with_name('concurrency_controlled_codex.py').resolve())
    original_create = ManagedProcess._create
    original_run = web.run_managed_process

    def create(process, command, cwd, env, files):
        # This is the executable trust boundary, not a replacement execution result.
        parser = argparse.ArgumentParser(exit_on_error=False)
        parser.add_argument('--sandbox', required=True)
        parser.add_argument('--cd', type=Path, required=True)
        parser.add_argument('--json', action='store_true', required=True)
        parser.add_argument('--skip-git-repo-check', action='store_true', required=True)
        parser.add_argument('--output-last-message', type=Path, required=True)
        if command[:2] != [marker, 'exec']:
            raise ValueError('Fixture rejects non-controlled executable')
        options, tail = parser.parse_known_args(command[2:])
        if tail != ['-'] and (len(tail) != 3 or tail[0] != 'resume' or tail[-1] != '-'):
            raise ValueError('Unsupported controlled resume command')
        answer = options.output_last_message.resolve()
        if not answer.is_relative_to(root / 'temp') or Path(cwd).resolve() != root / 'workspace' or options.cd.resolve() != Path(cwd).resolve():
            raise ValueError('Fixture command escaped isolated directories')
        attempt = uuid.uuid4().hex
        attempt_dir = root / 'attempts' / attempt
        attempt_dir.mkdir()
        process.fixture_attempt = attempt
        session = tail[1] if len(tail) == 3 else str(uuid.uuid4())
        translated = [str(Path(sys.executable).resolve()), script,
                      '--attempt-dir', str(attempt_dir), '--answer', str(answer), '--session', session]
        process.fixture_command = translated[:2]
        return original_create(process, translated, cwd, env, files)

    def run(message_id, command, **kwargs):
        execution = web.active_run(message_id)['execution']
        previous = kwargs.pop('on_created', None)

        def created(process):
            event = {'message_id': message_id, 'attempt': process.fixture_attempt,
                     'pid': process.pid, 'created_ns': time.time_ns(),
                     'logical_executable': command[0],
                     'actual_executable': process.fixture_command[0],
                     'actual_script': process.fixture_command[1]}
            publish(root / 'attempts' / process.fixture_attempt / 'created.json', event)
            if previous:
                previous(process)

        try:
            return original_run(message_id, command, on_created=created, **kwargs)
        finally:
            process = execution.process
            if execution.result is not None and hasattr(process, 'fixture_attempt'):
                result = execution.result
                original_error = sys.exception()
                try:
                    event = {'message_id': message_id, 'pid': process.pid,
                             'outcome': result.outcome, 'exit_evidence': result.exit_evidence,
                             'returncode': result.returncode}
                    if result.outcome != 'completed' or result.returncode != 0:
                        event['diagnostics'] = {
                            'stdout': diagnostic_excerpt(result.stdout),
                            'stderr': diagnostic_excerpt(result.stderr)}
                    publish(root / 'attempts' / process.fixture_attempt / 'exit.json', event)
                except OSError as observation_error:
                    if original_error is None:
                        raise
                    original_error.add_note('Exit observation failed: ' + str(observation_error))

    ManagedProcess._create = create
    web.run_managed_process = run
    web.resolve_codex_command = lambda: marker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--lifetime', required=True, type=float)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    if not root.is_dir() or any(root.iterdir()):
        parser.error('--root must be a newly created empty directory')
    if not 0 < args.lifetime <= 60:
        parser.error('--lifetime must be positive and at most 60 seconds')
    for name in ('workspace', 'sessions', 'temp', 'attempts'):
        (root / name).mkdir()
    database = root / 'fixture.db'
    os.environ['AGENT_BRIDGE_DB'] = str(database)
    for name in ('HOME', 'USERPROFILE'):
        os.environ[name] = str(root / 'sessions')
    for name in ('TEMP', 'TMP'):
        os.environ[name] = str(root / 'temp')
    for name in ('CODEX_HOME', 'APPDATA', 'LOCALAPPDATA'):
        os.environ[name] = str(root / 'sessions')
    for name in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP'):
        os.environ.pop(name, None)
    for name in list(os.environ):
        if name.startswith('FASTMCP_'):
            del os.environ[name]
    (root / 'workspace' / 'specs').mkdir()
    (root / 'workspace' / 'AGENTS.md').write_text(
        '# Isolated test fixture\nUse specs/README.md for this controlled fixture.\n', encoding='utf-8')
    (root / 'workspace' / 'specs' / 'README.md').write_text(
        '# Isolated test fixture specifications\nOnly the controlled fixture request applies.\n',
        encoding='utf-8')
    os.chdir(root / 'workspace')

    import agent_bridge
    import web_chat as web

    def disabled_backend(*args, **kwargs):
        raise RuntimeError('Controlled backend wiring has not been installed')

    web.resolve_dsh_command = disabled_backend
    web.resolve_grok_command = disabled_backend
    install_controlled_backend(web, root)
    owned = {}
    admission_lock = threading.Lock()
    closing = False
    register = web.register_active_run
    worker = web.run_agent_task

    def register_owned(message_id, conversation_id, agent_id):
        with admission_lock:
            if closing:
                raise RuntimeError('Fixture has stopped accepting requests')
            register(message_id, conversation_id, agent_id)
            owned[message_id] = (web.active_run(message_id), threading.Event())

    def worker_owned(agent_id, conversation_id, prompt, message_id, user_message_id):
        try:
            return worker(agent_id, conversation_id, prompt, message_id, user_message_id)
        finally:
            owned[message_id][1].set()

    web.register_active_run = register_owned
    web.run_agent_task = worker_owned
    if web.DB_PATH.resolve() != database or agent_bridge.DB_PATH != database:
        raise RuntimeError('Fixture database isolation mismatch')
    with web.startup_server(0) as server:
        server.timeout = 0.1
        ready = {'pid': os.getpid(), 'port': server.server_address[1],
                 'instance_id': web.SERVICE_INSTANCE_ID, 'database': str(database),
                 'backend': 'controlled-codex'}
        publish(root / 'ready.json', ready)
        deadline = time.monotonic() + args.lifetime
        try:
            while time.monotonic() < deadline and not (root / 'stop').exists():
                server.handle_request()
        finally:
            with admission_lock:
                closing = True
            web.stop_all_active_runs()
            cleanup_deadline = time.monotonic() + 12
            for run, returned in owned.values():
                returned.wait(max(0, cleanup_deadline - time.monotonic()))
            clean = all(returned.is_set() and (run['execution'] is None or
                        (run['execution'].done.is_set() and run['execution'].released))
                        for run, returned in owned.values())
            publish(root / 'cleanup.json', {'state': 'confirmed' if clean else 'unknown'})
            if not clean:
                raise RuntimeError('Unknown cleanup; retain isolated database and evidence, never reuse instance')


if __name__ == '__main__':
    main()
