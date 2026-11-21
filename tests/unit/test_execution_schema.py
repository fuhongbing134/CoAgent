"""Version-2 execution metadata; no production database is opened."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
from workbench.storage import database_schema as schema
from workbench.storage.schema_definition import SCHEMA_SQL


class ExecutionSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'execution.db'

    def v1(self):
        with closing(sqlite3.connect(self.path)) as db,db:
            db.executescript(SCHEMA_SQL)
            db.execute("INSERT INTO workbench_sessions VALUES ('room','thread-a','old')")
            db.executemany('INSERT INTO workbench_agent_sessions VALUES (?,?,?,?)',[
                ('room','codex','thread-b','new'),
                ('room','deepseek-harness','old-dsh','old'),
                ('room','deepseek-harness-headless','new-dsh','new'),
                ('room','discussion:hash:codex','discussion-id','stamp'),
                ('room','unknown','unknown-id','stamp')])
            schema._legacy_sessions(db)
            db.execute('PRAGMA user_version=1')

    def state(self):
        with closing(sqlite3.connect(self.path)) as db:
            return list(db.iterdump()), db.execute('PRAGMA user_version').fetchone()[0]

    def test_v1_upgrade_preserves_every_candidate_and_old_table(self):
        self.v1()
        with closing(sqlite3.connect(self.path)) as db:
            originals={table:db.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall() for table in ('workbench_sessions','workbench_agent_sessions','legacy_session_imports')}
        backup=schema.ensure_schema(self.path,allow_legacy=True)
        self.assertIn('.pre-v2-',backup.name)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],2)
            for table,rows in originals.items():
                self.assertEqual(db.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall(),rows)
            rows=db.execute('SELECT agent_id,conversation_id,session_id,backend_scope,generation,state,legacy_candidates_json FROM execution_sessions').fetchall()
            self.assertEqual({r[0] for r in rows},{'codex','deepseek-harness','discussion:hash:codex','unknown'})
            all_candidates=[c for r in rows for c in json.loads(r[-1])]
            self.assertEqual(len(all_candidates),6)
            self.assertEqual({c['session_id'] for c in all_candidates},{'thread-a','thread-b','old-dsh','new-dsh','discussion-id','unknown-id'})
            self.assertTrue(all(r[2:6]==(None,None,0,'needs_validation') for r in rows))
            self.assertEqual(db.execute('SELECT count(*) FROM execution_runs').fetchone()[0],0)
            self.assertEqual(db.execute("SELECT count(*) FROM execution_domains WHERE state!='idle' OR owner_run_id IS NOT NULL OR fence!=0").fetchone()[0],0)
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0],'wal')
        before=self.state()
        self.assertIsNone(schema.ensure_schema(self.path))
        self.assertEqual(self.state(),before)

    def test_v1_rollback_at_each_stage(self):
        self.v1();before=self.state()
        for stage in ('ddl','data','version'):
            with self.subTest(stage=stage),self.assertRaisesRegex(RuntimeError,'injected'):
                schema.ensure_schema(self.path,allow_legacy=True,fail_at=stage)
            self.assertEqual(self.state(),before)

    def test_wal_failure_precedes_migration(self):
        self.v1();before=self.state()
        with patch.object(schema,'_enable_wal',side_effect=sqlite3.OperationalError('injected lock')):
            with self.assertRaisesRegex(RuntimeError,'before schema migration; schema/data/version not migrated'):
                schema.ensure_schema(self.path,allow_legacy=True)
        self.assertEqual(self.state(),before)
        schema.ensure_schema(self.path,allow_legacy=True)
        self.assertEqual(self.state()[1],2)

    def test_wal_mode_must_be_accepted(self):
        from unittest.mock import Mock
        db=Mock();db.execute.return_value.fetchone.return_value=('delete',)
        with self.assertRaisesRegex(RuntimeError,'did not accept WAL'):
            schema._enable_wal(db)

    def test_domain_pk_sources_and_session_uniqueness(self):
        schema.ensure_schema(self.path)
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute("INSERT INTO execution_runs(run_id,agent_id,conversation_id,source_kind,source_job_id,instance_id,config_snapshot,state) VALUES ('r1','codex','room','owner','job-a','instance','{}','reserved')")
            db.execute("INSERT INTO execution_runs(run_id,agent_id,conversation_id,source_kind,source_job_id,instance_id,config_snapshot,state) VALUES ('r2','codex','room','final','job-b','instance','{}','reserved')")
            db.execute("INSERT INTO execution_domains(agent_id,conversation_id) VALUES ('codex','room')")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO execution_domains(agent_id,conversation_id) VALUES ('codex','room')")
            db.execute("INSERT INTO execution_sessions(agent_id,conversation_id,backend_scope,session_id) VALUES ('codex','a','scope','same')")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO execution_sessions(agent_id,conversation_id,backend_scope,session_id) VALUES ('codex','b','scope','same')")
            db.execute("INSERT INTO execution_sessions(agent_id,conversation_id,backend_scope,session_id) VALUES ('codex','b','other-scope','same')")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE execution_sessions SET state='ready' WHERE conversation_id='a'")

    def test_v1_requires_coordinated_upgrade_ack(self):
        self.v1();before=self.state()
        with self.assertRaisesRegex(RuntimeError,'old writers'):
            schema.ensure_schema(self.path)
        self.assertEqual(self.state(),before)


if __name__=='__main__': unittest.main()
