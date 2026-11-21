"""Isolated real-schema migrations and OS permit integration tests."""
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from workbench.storage.database_schema import ensure_schema, restore_backup, SCHEMA_VERSION
from workbench.execution.migration_barrier import MigrationBarrier
import web_chat as web


def snapshot(path):
    with closing(sqlite3.connect(path)) as db:
        return list(db.iterdump())


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'test.db'

    def legacy(self, duplicate=False):
        db = sqlite3.connect(self.path)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA wal_autocheckpoint=0')
        db.execute('CREATE TABLE workbench_sessions(conversation_id TEXT PRIMARY KEY,codex_thread_id TEXT NOT NULL,updated_at TEXT NOT NULL)')
        db.execute('CREATE TABLE workbench_agent_sessions(conversation_id TEXT NOT NULL,agent_id TEXT NOT NULL,session_id TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(conversation_id,agent_id))')
        db.execute("CREATE TABLE workbench_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,conversation_id TEXT NOT NULL,sender_id TEXT NOT NULL,recipient_id TEXT NOT NULL,content TEXT NOT NULL,created_at TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('pending','completed','failed')))")
        db.execute("INSERT INTO workbench_messages VALUES (1,'room','user','codex','history','stamp','completed')")
        db.executemany('INSERT INTO workbench_sessions VALUES (?,?,?)',[('a','thread-a','stamp'),('b','thread-a' if duplicate else 'thread-b','stamp')])
        db.execute("INSERT INTO workbench_agent_sessions VALUES ('a','deepseek-harness-headless','session-a','stamp')")
        db.commit()
        return db

    def test_mapping_wal_backup_restore_and_idempotence(self):
        with closing(self.legacy()) as live:
            before=snapshot(self.path)
            backup=ensure_schema(self.path,allow_legacy=True)
            self.assertEqual(snapshot(backup),before)
            with closing(sqlite3.connect(self.path)) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],SCHEMA_VERSION)
                self.assertEqual(db.execute('SELECT count(*) FROM legacy_session_imports').fetchone()[0],3)
                self.assertEqual(db.execute("SELECT DISTINCT state FROM legacy_session_imports").fetchall(),[('needs_validation',)])
                self.assertEqual(db.execute('SELECT content FROM workbench_messages').fetchone()[0],'history')
                self.assertIn("'cancelled'",db.execute("SELECT sql FROM sqlite_master WHERE name='workbench_messages'").fetchone()[0])
            after=snapshot(self.path)
            self.assertIsNone(ensure_schema(self.path))
            self.assertEqual(snapshot(self.path),after)
        restore_backup(self.path,backup,writers_stopped=True)
        self.assertEqual(snapshot(self.path),before)
        with self.assertRaisesRegex(RuntimeError,'writers stopped'): restore_backup(self.path,backup)

    def test_rollback_every_stage(self):
        with closing(self.legacy()):
            before=snapshot(self.path)
            for stage in ('ddl','data','version'):
                with self.subTest(stage=stage):
                    with self.assertRaisesRegex(RuntimeError,'injected'): ensure_schema(self.path,allow_legacy=True,fail_at=stage)
                    self.assertEqual(snapshot(self.path),before)
                    with closing(sqlite3.connect(self.path)) as db:
                        self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],0)

    def imports(self):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute('SELECT agent_key,conversation_id,session_id,updated_at,source_table,state FROM legacy_session_imports ORDER BY agent_key,conversation_id').fetchall()

    def test_duplicate_bindings_retained_as_candidates(self):
        with closing(self.legacy(duplicate=True)):
            ensure_schema(self.path,allow_legacy=True)
        rows = self.imports()
        codex = [row for row in rows if row[4]=='workbench_sessions']
        self.assertEqual(len(codex),2)
        self.assertEqual({row[2] for row in codex},{'thread-a'})
        self.assertEqual(len({row[0] for row in codex}),2)
        self.assertTrue(all(row[0].startswith('legacy-source:') for row in codex))
        self.assertTrue(all(row[-1]=='needs_validation' for row in rows))

    def test_unknown_and_discussion_preserved_and_reported_together(self):
        import json
        with closing(self.legacy()) as db:
            db.executemany('INSERT INTO workbench_agent_sessions VALUES (?,?,?,?)',[
                ('a','deepseek-harness','older-id','old-stamp'),
                ('a','discussion:hash:codex','discussion-id','stamp'),
                ('a','discussion:other:deepseek-harness','other-id','stamp'),
                ('a','unknown','unknown-id','stamp')])
            db.commit()
            before = db.execute('SELECT * FROM workbench_agent_sessions ORDER BY agent_id').fetchall()
            with patch('workbench.storage.database_schema.logging.warning') as warning:
                ensure_schema(self.path,allow_legacy=True)
            warning.assert_called_once()
            items = json.loads(warning.call_args.args[1])
            self.assertEqual(len(items),7)
            keys={row[0] for row in self.imports()}
            self.assertTrue({'deepseek-harness','discussion:hash:codex','discussion:other:deepseek-harness','unknown','deepseek-harness-headless','codex'} <= keys)
            self.assertTrue(all(item['state']=='needs_validation' for item in items))
            self.assertEqual(db.execute('SELECT * FROM workbench_agent_sessions ORDER BY agent_id').fetchall(),before)
            self.assertEqual({row[2] for row in self.imports() if row[0] in ('deepseek-harness','deepseek-harness-headless')},{'older-id','session-a'})

    def test_same_domain_conflict_preserves_both_sources(self):
        import json
        with closing(self.legacy()) as db:
            db.execute("INSERT INTO workbench_agent_sessions VALUES ('a','codex','other-thread','newer-stamp')")
            db.commit()
            ensure_schema(self.path,allow_legacy=True)
        rows=[row for row in self.imports() if row[1]=='a' and row[4] in ('workbench_sessions','workbench_agent_sessions') and row[0].startswith('legacy-source:')]
        self.assertEqual(len(rows),2)
        self.assertEqual({row[2] for row in rows},{'thread-a','other-thread'})
        for row in rows:
            self.assertEqual(json.loads(row[0].split(':',1)[1]),[row[4],'codex','a'])
        after=snapshot(self.path)
        self.assertIsNone(ensure_schema(self.path))
        self.assertEqual(snapshot(self.path),after)

    def test_reserved_prefix_collision_and_empty_ids_preserved(self):
        with closing(self.legacy(duplicate=True)) as db:
            db.execute("INSERT INTO workbench_agent_sessions VALUES ('a','legacy-source:anything','','stamp')")
            db.commit()
            ensure_schema(self.path,allow_legacy=True)
        rows=self.imports()
        self.assertIn(('legacy-source:anything','a','','stamp','workbench_agent_sessions','needs_validation'),rows)
        self.assertEqual(len(rows),4)
        self.assertTrue(all(row[0].startswith('_legacy-source:') for row in rows if row[4]=='workbench_sessions'))

    def test_future_version_and_legacy_ack_before_ddl(self):
        with closing(self.legacy()):
            before=snapshot(self.path)
            with self.assertRaisesRegex(RuntimeError,'old writers'): ensure_schema(self.path)
            self.assertEqual(snapshot(self.path),before)
        with closing(sqlite3.connect(self.path)) as db, db: db.execute('PRAGMA user_version=99')
        before=snapshot(self.path)
        with self.assertRaisesRegex(RuntimeError,'future'): ensure_schema(self.path,allow_legacy=True)
        self.assertEqual(snapshot(self.path),before)
        self.assertFalse(list(self.path.parent.glob('*.backup')))
        with patch.object(web,'DB_PATH',self.path),patch.object(web,'initialize_workbench_tables') as init:
            with self.assertRaisesRegex(RuntimeError,'future'):
                with web.startup_server(0): pass
            init.assert_not_called()

    def test_shared_exclusive_process_mutex(self):
        code="import sys; from workbench.execution.migration_barrier import MigrationBarrier;\nwith MigrationBarrier(sys.argv[1],exclusive=sys.argv[2]=='1'): pass"
        def child(exclusive):
            return subprocess.run([sys.executable,'-B','-c',code,str(self.path),'1' if exclusive else '0'],capture_output=True,text=True,timeout=10)
        with MigrationBarrier(self.path):
            self.assertEqual(child(False).returncode,0)
            self.assertNotEqual(child(True).returncode,0)
        with MigrationBarrier(self.path,exclusive=True):
            self.assertNotEqual(child(False).returncode,0)
            self.assertNotEqual(child(True).returncode,0)
        self.assertEqual(child(True).returncode,0)

    def test_bridge_transaction_holds_permit_and_no_ddl(self):
        import agent_bridge as bridge
        ensure_schema(self.path)
        with patch.object(bridge.bridge,'DB_PATH',self.path):
            with bridge.database() as db:
                seen=[]
                db.set_trace_callback(seen.append)
                db.execute("INSERT INTO agents VALUES ('test','n','v','m','r','t','t')")
                with self.assertRaises(RuntimeError):
                    with MigrationBarrier(self.path,exclusive=True): pass
            self.assertFalse(any('CREATE ' in sql.upper() for sql in seen))
            with MigrationBarrier(self.path,exclusive=True):
                with self.assertRaises(RuntimeError):
                    with bridge.database(): pass
            with bridge.database() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM agents').fetchone()[0],1)

    def test_bridge_refuses_version_without_initializing(self):
        import agent_bridge as bridge
        with closing(self.legacy()): pass
        before=snapshot(self.path)
        with patch.object(bridge.bridge,'DB_PATH',self.path):
            with self.assertRaisesRegex(RuntimeError,'Schema version'):
                with bridge.database(): pass
        self.assertEqual(snapshot(self.path),before)

    def test_transaction_is_invisible_until_commit(self):
        from workbench.storage import database_schema as schema
        with closing(self.legacy()):
            before = snapshot(self.path)
            original = schema._legacy_sessions
            observed = []
            def observe(db):
                observed.append(snapshot(self.path))
                original(db)
                observed.append(snapshot(self.path))
            with patch.object(schema, '_legacy_sessions', observe):
                ensure_schema(self.path, allow_legacy=True)
            self.assertEqual(observed, [before,before])
            self.assertNotEqual(snapshot(self.path), before)

    def test_inconsistent_version_marker_rejected(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('PRAGMA user_version=1')
        with self.assertRaisesRegex(RuntimeError, 'inconsistent'):
            ensure_schema(self.path)

    def test_web_connection_holds_permit(self):
        ensure_schema(self.path)
        with patch.object(web,'DB_PATH',self.path),closing(web.connect_workbench()) as db:
            db.execute('SELECT 1')
            with self.assertRaises(RuntimeError):
                with MigrationBarrier(self.path,exclusive=True): pass
        with MigrationBarrier(self.path,exclusive=True): pass


if __name__=='__main__': unittest.main()
