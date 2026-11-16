"""Read-only DB/raw-sample reconciliation for completed benchmark cases."""
from contextlib import closing
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from benchmark_concurrency import checked_root


def audit(root):
    results = []
    for case in sorted(root.glob('case-*')):
        if not (case/'samples.jsonl').exists():
            continue
        config = json.loads((case/'config.json').read_text(encoding='utf-8'))
        # Correctness intentionally retains old occupancy and staged replies.
        if config['mode'] != 'load':
            continue
        with closing(sqlite3.connect((case/'isolated.db').resolve().as_uri()+'?mode=ro', uri=True)) as db:
            rows = [json.loads(line) for line in (case/'samples.jsonl').read_text(encoding='utf-8').splitlines()]
            assert len(rows) == config['warmup']+config['samples'], 'Missing sample'
            assert len({r['request_id'] for r in rows}) == len(rows), 'Repeated request ID'
            for row in rows:
                assert row['status'] == 'completed' and row['http_status'] == 202
                mids = row['message_id']
                if isinstance(mids, int):
                    mids = [mids]  # Earlier exploratory raw schema.
                for mid in mids:
                    message = db.execute('SELECT conversation_id,sender_id,content,status FROM workbench_messages WHERE id=?', (mid,)).fetchone()
                    assert message == (row['conversation'], row['agent'], str(mid), 'completed'), (case.name, mid, message)
                    runs = db.execute('SELECT conversation_id,agent_id,state FROM execution_runs WHERE source_message_id=?', (mid,)).fetchall()
                    assert runs == [(row['conversation'], row['agent'], 'completed')], (mid, runs)
            assert db.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
            assert db.execute('PRAGMA foreign_key_check').fetchall() == []
            assert db.execute("SELECT count(*) FROM execution_domains WHERE state!='idle'").fetchone()[0] == 0
        results.append(dict(case=case.name, samples=len(rows), status='passed',
                            raw_sha256=hashlib.sha256((case/'samples.jsonl').read_bytes()).hexdigest()))
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(audit(checked_root(args.output)), indent=2))
