"""Read-only 4B smoke checks. No CLI launch, POST, DB initialization or migration.

Exit codes: 0 automatic checks passed; 1 a check failed; 2 inconclusive checks.
Manual session/cancellation/descendant checks are outside this script's verdict.
"""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

sys.dont_write_bytecode = True


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def get_json(base, endpoint):
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(Request(base + endpoint, method='GET'), timeout=5) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError('HTTP response too large')
        return json.loads(raw)


def read_database(path, recent):
    # mode=ro refuses a missing database; do not use immutable on a live WAL DB.
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        version = db.execute('PRAGMA user_version').fetchone()[0]
        domains = [dict(r) for r in db.execute('''SELECT agent_id,conversation_id,state,
            owner_run_id,instance_id,fence FROM execution_domains ORDER BY agent_id,conversation_id''')]
        runs = [dict(r) for r in db.execute('''SELECT run_id,agent_id,conversation_id,state,
            instance_id,source_message_id,source_job_id,source_call_id,cancel_requested,
            exit_confirmed,config_snapshot FROM execution_runs ORDER BY rowid DESC LIMIT ?''', (recent,))]
        owners = [dict(r) for r in db.execute('''SELECT d.agent_id,d.conversation_id,d.state,
            d.owner_run_id,d.instance_id,r.run_id AS found_run,r.agent_id AS run_agent,
            r.conversation_id AS run_conversation,r.instance_id AS run_instance,r.state AS run_state
            FROM execution_domains d LEFT JOIN execution_runs r ON r.run_id=d.owner_run_id
            WHERE d.state!='idle' ''')]
        db.rollback()
    return version, domains, runs, owners


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8765')
    parser.add_argument('--db', type=Path, default=Path(os.environ.get(
        'AGENT_BRIDGE_DB', Path(__file__).resolve().parent / 'agent_bridge.db')))
    parser.add_argument('--conversation', default='default')
    parser.add_argument('--recent', type=int, default=12)
    args = parser.parse_args(argv)
    base = args.base_url.rstrip('/')
    url = urlsplit(base)
    if (url.scheme != 'http' or url.hostname not in {'127.0.0.1', 'localhost'}
            or url.username or url.password or url.path or url.query or url.fragment):
        parser.error('--base-url must be a local http://127.0.0.1:PORT or localhost URL')
    if not 1 <= args.recent <= 100 or not 1 <= len(args.conversation) <= 200:
        parser.error('recent must be 1..100; conversation length must be 1..200')
    results = []

    def check(name, status, detail):
        results.append(status)
        print(f'{status} {name}: ' + json.dumps(detail, ensure_ascii=True), flush=True)

    print('READ ONLY: GET + SQLite mode=ro; no CLI/session launch. UNVERIFIED means 未验收.')
    print('Database: ' + str(args.db.expanduser().resolve()))
    print('Ensure this is the database used by the running service; HTTP does not expose its DB path.')
    for endpoint in ('/api/agents', '/api/capabilities?' + urlencode({'conversation_id': args.conversation})):
        try:
            data = get_json(base, endpoint)
            if endpoint == '/api/agents':
                ids = {a['agent_id'] for a in data['agents']}
                found = {'codex': data.get('codex_cli_available'), 'deepseek-harness': data.get('dsh_cli_available')}
                ok = {'codex', 'deepseek-harness'} <= ids and all(v is True for v in found.values())
            else:
                found = {a['id']: a.get('cli_found') for a in data['agent_configuration']}
                ok = all(found.get(a) is True for a in ('codex','deepseek-harness'))
            check(endpoint, 'PASS' if ok else 'FAIL', found)
        except Exception as error:
            check(endpoint, 'FAIL', {'error_type': type(error).__name__, 'message': str(error)[:300]})
    try:
        import web_chat
        for name, resolver in [('codex', web_chat.resolve_codex_command), ('deepseek-harness', web_chat.resolve_dsh_command)]:
            try:
                executable = resolver()
                check('local_cli_' + name, 'PASS' if executable else 'FAIL', executable)
            except PermissionError as error:
                check('local_cli_' + name, 'UNVERIFIED', {'error_type': type(error).__name__,
                      'winerror': getattr(error, 'winerror', None), 'note': 'This probe environment cannot access the path; not proof of service failure.'})
            except Exception as error:
                check('local_cli_' + name, 'FAIL', {'error_type': type(error).__name__, 'message': str(error)[:300]})
    except Exception as error:
        check('local_import', 'FAIL', {'error_type': type(error).__name__})
    try:
        version, domains, runs, owners = read_database(args.db.expanduser().resolve(), args.recent)
        check('schema_version', 'PASS' if version == 2 else 'FAIL', version)
        bad_owners = [r for r in owners if not r['found_run'] or r['run_agent'] != r['agent_id']
                      or r['run_conversation'] != r['conversation_id'] or r['run_instance'] != r['instance_id']
                      or r['run_state'] not in {'pending','running','quiescing'}]
        check('domain_ownership', 'FAIL' if bad_owners else 'PASS', bad_owners)
        # An active invocation (including this collaborator) is not a stale run.
        # A one-time read cannot prove liveness/staleness; never guess from age.
        check('domains_idle', 'UNVERIFIED' if owners else 'PASS', {
            'domains': domains, 'note': 'Non-idle may be current work or retained cleanup; rerun after work finishes. No automatic release.'})
        valid = {'pending','running','quiescing','completed','failed','cancelled'}
        bad_runs = [r['run_id'] for r in runs if r['state'] not in valid or not r['instance_id']]
        check('recent_run_states_and_instances', 'FAIL' if bad_runs else 'PASS' if runs else 'UNVERIFIED',
              {'invalid_runs': bad_runs, 'count': len(runs)})
        for row in runs:
            snapshot = json.loads(row.pop('config_snapshot'))
            row['launch_mode'] = snapshot.get('launch_mode')
            # No prompts, session IDs, environment, or credential paths printed.
            print('RUN ' + json.dumps(row, ensure_ascii=True))
    except Exception as error:
        check('database_read', 'FAIL', {'error_type': type(error).__name__, 'message': str(error)[:300]})
    code = 1 if 'FAIL' in results else 2 if 'UNVERIFIED' in results else 0
    print('SUMMARY ' + json.dumps({'PASS': results.count('PASS'), 'FAIL': results.count('FAIL'),
          'UNVERIFIED': results.count('UNVERIFIED'), 'exit_code': code,
          'manual_tests': 'not_tested', 'full_backend_acceptance': 'not_tested'}))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
