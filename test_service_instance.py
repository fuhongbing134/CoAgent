import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch

import web_chat as web
from service_instance import ServiceInstanceLock


class InstanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'isolated.db'
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE sentinel(value)')
            db.execute('INSERT INTO sentinel VALUES (42)')

    def child(self, code):
        return subprocess.run([sys.executable, '-B', '-c', code, str(self.path)], capture_output=True, text=True, timeout=15)

    def test_import_has_no_database_side_effect(self):
        before = self.path.read_bytes()
        result = self.child("import os,sys; os.environ['AGENT_BRIDGE_DB']=sys.argv[1]; import agent_bridge,web_chat")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, self.path.read_bytes())
        missing = Path(self.temp.name) / 'missing.db'
        result = subprocess.run([sys.executable, '-B', '-c', 'import agent_bridge,web_chat'], env={**os.environ, 'AGENT_BRIDGE_DB':str(missing)}, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(missing.exists())

    def test_second_service_different_ports_no_writes(self):
        before = self.path.read_bytes()
        with ServiceInstanceLock(self.path):
            for port in (0, 18765):
                result = self.child("import sys,web_chat as w; from pathlib import Path; w.DB_PATH=Path(sys.argv[1]);\nwith w.startup_server("+str(port)+"): pass")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('execution-service lock', result.stderr)
        self.assertEqual(before, self.path.read_bytes())

    def test_process_death_releases_lock(self):
        code = "import sys; from service_instance import ServiceInstanceLock; lock=ServiceInstanceLock(sys.argv[1]); lock.__enter__(); print('locked',flush=True); sys.stdin.read()"
        child = subprocess.Popen([sys.executable,'-B','-c',code,str(self.path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'locked')
            with self.assertRaises(RuntimeError):
                with ServiceInstanceLock(self.path): pass
            child.kill()
            child.wait(timeout=10)
            with ServiceInstanceLock(self.path) as lock:
                self.assertFalse(os.get_inheritable(lock._file.fileno()))
        finally:
            if child.poll() is None: child.kill()
            child.communicate(timeout=10)

    def test_port_conflict_before_initialization(self):
        import socket
        before = self.path.read_bytes()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); sock.listen()
            with patch.object(web,'DB_PATH',self.path), patch.object(web,'initialize_workbench_tables') as init:
                with self.assertRaises(OSError):
                    with web.startup_server(sock.getsockname()[1]): pass
                init.assert_not_called()
        self.assertEqual(before,self.path.read_bytes())
        with ServiceInstanceLock(self.path): pass

    def test_normal_start_and_cleanup_on_failure(self):
        with patch.object(web,'DB_PATH',self.path), patch.object(web,'COLLAB_SERVICE',None):
            with web.startup_server(0, allow_legacy=True):
                with closing(sqlite3.connect(self.path)) as db, db:
                    self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE name='collab_jobs'").fetchone())
            with patch.object(web,'initialize_workbench_tables',side_effect=RuntimeError('injected')):
                with self.assertRaisesRegex(RuntimeError,'injected'):
                    with web.startup_server(0, allow_legacy=True): pass
        with ServiceInstanceLock(self.path): pass

    @unittest.skipUnless(os.name == 'nt', 'Windows junction test')
    def test_junction_alias(self):
        alias = self.path.parent / 'junction'
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(self.path.parent)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        try:
            with ServiceInstanceLock(self.path):
                with self.assertRaises(RuntimeError):
                    with ServiceInstanceLock(alias / self.path.name): pass
        finally:
            os.rmdir(alias)

    def test_alias_and_hardlink(self):
        alias = self.path.parent / '.' / self.path.name.upper() if os.name=='nt' else self.path
        with ServiceInstanceLock(self.path):
            with self.assertRaises(RuntimeError):
                with ServiceInstanceLock(alias): pass
        linked = self.path.with_name('linked.db')
        os.link(self.path,linked)
        for path in (self.path,linked):
            with self.assertRaisesRegex(RuntimeError,'hard link'):
                with ServiceInstanceLock(path): pass


if __name__ == '__main__': unittest.main()
