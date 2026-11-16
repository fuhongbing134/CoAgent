"""Checks for unsafe target rejection and reproducible, conditional statistics."""
import json
from pathlib import Path
import tempfile
import sqlite3
import unittest

from benchmark_concurrency import checked_root, quantile, report, REPO
from audit_concurrency import audit


class BenchmarkTests(unittest.TestCase):
    def test_quantile_bounds_and_nearest_rank(self):
        self.assertIsNone(quantile([], .99))
        self.assertEqual(quantile(list(range(1, 101)), .99), 99)
        self.assertEqual(quantile([7], .99), 7)
        for p in [0, 99, -1]:
            with self.assertRaises(ValueError):
                quantile([1], p)

    def test_refuses_existing_directory_and_external_target(self):
        with self.assertRaises(FileExistsError):
            checked_root(REPO/'docs', create=True)
        with self.assertRaises(ValueError):
            checked_root(REPO.parent/'external')

    def test_report_keeps_censoring_out_of_success_quantiles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = root/'case-000'
            case.mkdir()
            rows = [dict(warmup=False, status='completed', censored=False,
                         http_status=202, t_send=0, t_observed=10_000_000),
                    dict(warmup=False, status='timeout', censored=True,
                         http_status=202, t_send=0, t_observed=30_000_000_000)]
            (case/'samples.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows), encoding='utf-8')
            result = report(root)[0]
            self.assertEqual((result['n'], result['successful'], result['censored']), (2,1,1))
            self.assertEqual(result['p99_ms'], 10)
            self.assertAlmostEqual(result['cohort_success_per_second'], 1/30)

    def test_audit_detects_cross_conversation_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = root/'case-000'
            case.mkdir()
            (case/'config.json').write_text(json.dumps(dict(mode='load',warmup=0,samples=1)))
            (case/'samples.jsonl').write_text(json.dumps(dict(request_id='r',status='completed',http_status=202,
                message_id=[1],conversation='a',agent='codex'))+'\n')
            db = sqlite3.connect(case/'isolated.db')
            try:
                db.executescript('''CREATE TABLE workbench_messages(id,conversation_id,sender_id,content,status);
                    CREATE TABLE execution_runs(source_message_id,conversation_id,agent_id,state);
                    CREATE TABLE execution_domains(state);
                    INSERT INTO workbench_messages VALUES (1,'a','codex','1','completed');
                    INSERT INTO execution_runs VALUES (1,'a','codex','completed');''')
                self.assertEqual(len(audit(root)), 1)
                db.execute("UPDATE workbench_messages SET conversation_id='wrong'")
                db.commit()
                with self.assertRaises(AssertionError):
                    audit(root)
            finally:
                db.close()


if __name__ == '__main__':
    unittest.main()
