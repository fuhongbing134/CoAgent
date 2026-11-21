"""Finite, file-controlled Codex protocol producer; never invokes a model."""
import argparse
import json
from pathlib import Path
import sys
import time


def entry_response(attempt):
    payload = {
        'protocol_version': 1,
        'status': 'completed',
        'questions': [],
        'handoff': {
            'original_question': 'Controlled fixture request',
            'response_or_changes': 'Controlled fixture reply',
            'evidence': 'controlled:' + attempt,
            'rejected_alternatives': 'None',
            'uncertainties': 'None',
            'expected_action': 'None',
        },
        'plan': {'owner_id': 'codex', 'final_responder_id': 'codex', 'tasks': []},
    }
    return f'controlled:{attempt}\n\n```agent-collaboration\n{json.dumps(payload)}\n```'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--attempt-dir', type=Path, required=True)
    parser.add_argument('--answer', type=Path, required=True)
    parser.add_argument('--session', required=True)
    args = parser.parse_args()
    attempt = args.attempt_dir.name
    prompt_bytes = len(sys.stdin.buffer.read())
    print(json.dumps({'type': 'thread.started', 'thread_id': args.session}), flush=True)
    ready = args.attempt_dir / 'ready.tmp'
    ready.write_text(json.dumps({
        'attempt': attempt, 'session': args.session, 'prompt_bytes': prompt_bytes
    }), encoding='utf-8')
    ready.replace(args.attempt_dir / 'ready.json')
    deadline = time.monotonic() + 60
    release = args.attempt_dir / 'release'
    while time.monotonic() < deadline:
        if release.exists() and release.read_text(encoding='utf-8') == attempt:
            args.answer.write_text(entry_response(attempt), encoding='utf-8')
            return 0
        time.sleep(0.02)
    print('controlled release deadline exceeded', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
