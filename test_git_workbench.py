from contextlib import closing
import json
from pathlib import Path
import sqlite3
import unittest

from git_automation import GitAutomationError
from git_workbench import WorkbenchGit, build_server
import test_git_automation as fixtures


class WorkbenchIntegrationTests(unittest.TestCase):
    call = fixtures.GitAutomationTests.call
    # Reuse the real isolated Git fixture, without duplicating inherited tests.
    def setUp(self):
        fixtures.GitAutomationTests.setUp(self)
        root = Path(self.temp.name)
        self.db = root / 'host.db'
        with closing(sqlite3.connect(self.db)) as db, db:
            db.executescript('''
                CREATE TABLE collab_jobs(job_id TEXT, stopped INTEGER);
                CREATE TABLE collab_tasks(job_id TEXT, task_id TEXT, agent_id TEXT);
                CREATE TABLE collab_bindings(job_id TEXT, conversation_id TEXT);
                CREATE TABLE workbench_conversation_settings(conversation_id TEXT, working_directory TEXT);
                INSERT INTO collab_jobs VALUES('j1',0);
                INSERT INTO collab_tasks VALUES('j1','t1','codex');
                INSERT INTO collab_bindings VALUES('j1','c1');
            ''')
            db.execute('INSERT INTO workbench_conversation_settings VALUES(?,?)', ('c1',str(self.repo)))
        results = root / 'results'
        results.mkdir()
        self.config = root / 'host.json'
        self.settings = dict(database=str(self.db), approvals=str(self.store.directory),
            results=str(results), tasks={'t1': dict(job_id='j1', directory=str(self.repo),
            branch='feature/test', base_commit=self.base, remote='origin',
            executors=['codex','deepseek-harness','grok'])})
        self.config.write_text(json.dumps(self.settings),encoding='utf-8')
        self.host = WorkbenchGit(self.config, 'grok')

    def test_host_task_and_separate_operation(self):
        self.host.create_branch('t1','feature/test')
        (self.repo/'a.txt').write_text('host change\n')
        self.store.write('t1',dict(task_id='t1',branch='feature/test',base_commit=self.base,
            author='codex',reviewer='deepseek-harness',approved=True,
            content=self.host.api.content('t1',['a.txt'])))
        before = self.store._path('t1').read_bytes()
        result = self.host.commit_and_push('t1','feature/test',['a.txt'],'工作台提交')
        self.assertEqual(result['push_status'],'succeeded')
        self.assertEqual(self.store._path('t1').read_bytes(), before)
        self.assertEqual(self.host.reviews.read('t1')['operation']['commit_id'],result['commit_id'])
        self.assertEqual(self.host.commit_and_push('t1','feature/test',['a.txt'],'工作台提交'),result)

    def test_three_executors_and_only_two_mcp_tools(self):
        import asyncio
        for name in ('codex','deepseek-harness','grok'):
            host = WorkbenchGit(self.config,name)
            self.assertEqual(host.resolve_task('t1').executor,name)
            tools = asyncio.run(build_server(host).list_tools())
            self.assertEqual({t.name for t in tools},{'create_branch','commit_and_push'})

    def test_stop_directory_and_assignment_rejected(self):
        self.settings['tasks']['t1']['executors']=['codex']
        self.config.write_text(json.dumps(self.settings),encoding='utf-8')
        with self.assertRaises(GitAutomationError):
            WorkbenchGit(self.config,'grok').resolve_task('t1')
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('UPDATE collab_jobs SET stopped=1')
        with self.assertRaises(GitAutomationError):
            self.host.resolve_task('t1')
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('UPDATE collab_jobs SET stopped=0')
            db.execute('UPDATE workbench_conversation_settings SET working_directory=?',(str(self.repo.parent),))
        with self.assertRaises(GitAutomationError):
            self.host.resolve_task('t1')

    def test_execution_cannot_approve(self):
        self.store.write('t1',dict(approved=False))
        with self.assertRaises(GitAutomationError):
            self.host.reviews.write('t1',dict(approved=True,operation={}))
        self.assertFalse(self.store.read('t1')['approved'])

    def test_mcp_dispatch_and_missing_review(self):
        import asyncio
        server = build_server(self.host)
        asyncio.run(server.call_tool('create_branch', {'task_id':'t1','name':'feature/test'}))
        self.assertEqual(self.call('branch','--show-current'),'feature/test')
        with self.assertRaises(Exception):
            asyncio.run(server.call_tool('commit_and_push', dict(task_id='t1',
                branch='feature/test',files=['a.txt'],message='missing review')))
        self.assertEqual(self.call('rev-parse','HEAD'),self.base)

    def test_host_state_in_repository_rejected(self):
        bad = self.repo / 'host.json'
        bad.write_bytes(self.config.read_bytes())
        with self.assertRaises(GitAutomationError):
            WorkbenchGit(bad,'grok').resolve_task('t1')


if __name__ == '__main__':
    unittest.main()
