"""Single controlled HTTP round trip. Diagnostic limits are uncalibrated, not SLA."""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from collaboration_entry import parse_entry_response


def read_when_present(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def observe_child_ready(attempt_root, created, result, *, stage='waiting for ready'):
    ready = read_when_present(attempt_root / 'ready.json')
    exit_path = attempt_root / 'exit.json'
    exit_event = read_when_present(exit_path)
    if exit_event is not None:
        message_id = created['message_id']
        if exit_event['message_id'] != message_id or exit_event['pid'] != created['pid']:
            raise RuntimeError(f'Message {message_id}: {stage}: exit ownership mismatch; evidence={exit_path}')
        result.update(attempt=created['attempt'], exit=exit_event)
        evidence = exit_event['exit_evidence']
        description = ('child exited before release' if ready else 'child exited before ready')
        if evidence != 'confirmed':
            description = 'child result published with exit unconfirmed'
        raise RuntimeError(f"Message {message_id}: {stage}: {description}; "
                           f"returncode={exit_event['returncode']}, exit_evidence={evidence}; "
                           f"evidence={exit_path}")
    if ready is not None and ready['attempt'] != created['attempt']:
        raise RuntimeError('Controlled child attempt mismatch')
    return ready


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    root = output / 'instance'
    root.mkdir()
    source = Path(__file__).resolve().parent
    versions = {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                for name in ('concurrency_fixture_server.py',
                             'concurrency_controlled_codex.py',
                             'concurrency_fixture_supervisor.py',
                             'docs/design/CONCURRENCY_VERIFICATION_DESIGN.md',
                             'docs/requirements/CONCURRENCY_LOAD_TEST_REQUIREMENTS.md')}
    (output / 'versions.json').write_text(json.dumps(versions, indent=2), encoding='utf-8')
    environment = os.environ.copy()
    environment['AGENT_BRIDGE_DB'] = str(root / 'fixture.db')
    for name in ('HOME', 'USERPROFILE', 'CODEX_HOME', 'APPDATA', 'LOCALAPPDATA'):
        environment[name] = str(root / 'sessions')
    for name in ('TEMP', 'TMP'):
        environment[name] = str(root / 'temp')
    for name in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP'):
        environment.pop(name, None)
    for name in list(environment):
        if name.startswith('FASTMCP_'):
            del environment[name]
    deadline = time.monotonic() + 40
    result = {'state': 'unknown', 'diagnostic_seconds': 40, 'cleanup_seconds': 15}
    process = None

    def remaining():
        duration = deadline - time.monotonic()
        if duration <= 0:
            raise TimeoutError('Global diagnostic deadline exceeded')
        return duration

    def wait_for(observe):
        while True:
            remaining()
            value = observe()
            if value is not None:
                return value
            if process.poll() is not None:
                raise RuntimeError('Isolated server exited before observation')
            time.sleep(min(0.05, remaining()))

    def request(method, path, payload=None, expected=200):
        connection = http.client.HTTPConnection('127.0.0.1', ready['port'],
                                               timeout=min(2, remaining()))
        try:
            body = None if payload is None else json.dumps(payload)
            connection.request(method, path, body, {'Content-Type': 'application/json'})
            response = connection.getresponse()
            data = json.loads(response.read())
            if response.status != expected:
                raise RuntimeError(f'HTTP {path}: {response.status}')
            return data
        finally:
            connection.close()

    with (output / 'server.stdout').open('wb') as stdout, (output / 'server.stderr').open('wb') as stderr:
        try:
            process = subprocess.Popen(
                [str(Path(sys.executable).resolve()), str(source / 'concurrency_fixture_server.py'),
                 '--root', str(root), '--lifetime', '60'],
                cwd=source, env=environment, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
            result['server_pid'] = process.pid
            ready = wait_for(lambda: read_when_present(root / 'ready.json'))
            if ready['pid'] != process.pid or Path(ready['database']) != root / 'fixture.db':
                raise RuntimeError('Server ownership or database mismatch')
            conversation = 'fixture-' + uuid.uuid4().hex
            request('POST', '/api/settings', {'conversation_id': conversation,
                    'working_directory': str(root / 'workspace')})
            accepted = request('POST', '/api/messages', {'conversation_id': conversation,
                               'content': '@Codex controlled fixture', 'recipients': ['codex']}, 202)
            message_id = accepted['assistant_message_ids']['codex']
            result['message_id'] = message_id

            def terminal_message():
                messages = request('GET', '/api/messages?conversation_id=' + conversation)['messages']
                for message in messages:
                    if message['id'] == message_id and message['status'] in ('completed', 'failed', 'cancelled'):
                        return message
                return None


            def created_event():
                for path in (root / 'attempts').glob('*/created.json'):
                    event = read_when_present(path)
                    if event['message_id'] == message_id:
                        return event
                terminal = terminal_message()
                if terminal is not None and terminal['status'] in ('failed', 'cancelled'):
                    result['terminal_message'] = {
                        key: terminal[key] for key in ('id', 'status', 'content')}
                    raise RuntimeError(f"Message {message_id} {terminal['status']}: {terminal['content']}")
                return None

            created = wait_for(created_event)
            attempt = created['attempt']
            attempt_root = root / 'attempts' / attempt
            wait_for(lambda: observe_child_ready(attempt_root, created, result))
            release = attempt_root / 'release.tmp'
            release.write_text(attempt, encoding='utf-8')
            try:
                observe_child_ready(attempt_root, created, result, stage='before release')
                release.replace(attempt_root / 'release')
            finally:
                release.unlink(missing_ok=True)

            terminal = wait_for(terminal_message)
            exit_event = wait_for(lambda: read_when_present(attempt_root / 'exit.json'))
            if terminal['status'] != 'completed':
                raise RuntimeError(f"Entry message {message_id} {terminal['status']}: {terminal['content']}")
            answer, payload = parse_entry_response(terminal['content'])
            if (answer != 'controlled:' + attempt or payload['status'] != 'completed'
                    or payload['plan']['tasks']):
                raise RuntimeError('Entry reply did not match the controlled attempt')
            if (exit_event['exit_evidence'] != 'confirmed' or exit_event['returncode'] != 0
                    or exit_event['pid'] != created['pid']):
                raise RuntimeError('Actual process exit did not satisfy the round trip')
            result.update(state='completed', message_id=message_id, attempt=attempt,
                          created=created, exit=exit_event)
        except Exception as error:
            # Preserve failure, ownership and logs; never turn errors into passing evidence.
            result.update(state='failed', error_type=type(error).__name__, error=str(error))
        finally:
            if process is not None:
                (root / 'stop').touch()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    result.update(state='unknown', cleanup='server still owned; retained without forced kill')
                else:
                    cleanup = read_when_present(root / 'cleanup.json')
                    result['cleanup'] = cleanup
                    if process.returncode != 0 or cleanup != {'state': 'confirmed'}:
                        result.update(state='unknown', server_returncode=process.returncode)
            (output / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'state': result['state'], 'evidence': str(output)}))
    return 0 if result['state'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
