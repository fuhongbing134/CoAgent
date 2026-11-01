from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from git_automation import GitAutomation, GitAutomationError, GitTask, JsonReviewStore


class GitAutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo = root / 'repo'
        self.repo.mkdir()
        self.remote = root / 'remote.git'
        self.call('init', '-b', 'main')
        self.call('config', 'user.name', 'Test User')
        self.call('config', 'user.email', 'test@example.invalid')
        self.call('config', 'core.autocrlf', 'false')
        self.call('config', 'commit.gpgsign', 'false')
        (self.repo / 'a.txt').write_text('base\n')
        self.call('add', 'a.txt')
        self.call('commit', '-m', 'base')
        self.base = self.call('rev-parse', 'HEAD')
        subprocess.run(['git', 'init', '--bare', str(self.remote)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.call('remote', 'add', 'origin', str(self.remote))
        records = root / 'reviews'
        records.mkdir()
        self.store = JsonReviewStore(records)
        self.task = GitTask(self.repo, 'feature/test', self.base, 'origin', 'codex', 'grok')
        self.api = GitAutomation(lambda task_id: self.task if task_id == 't1' else None, self.store)

    def call(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode().strip()

    def prepare(self, files=None):
        self.api.create_branch('t1', 'feature/test')
        (self.repo / 'a.txt').write_text('changed\n')
        files = files or ['a.txt']
        self.store.write('t1', dict(task_id='t1', branch='feature/test', base_commit=self.base,
                                   author='codex', reviewer='deepseek-harness', approved=True,
                                   content=self.api.content('t1', files)))

    def test_create_and_push_exact_content_and_authorship(self):
        self.prepare()
        (self.repo / 'unrelated.txt').write_text('leave me')
        result = self.api.commit_and_push('t1', 'feature/test', ['a.txt'], 'feature')
        self.assertEqual(result['push_status'], 'succeeded')
        self.assertEqual(self.call('rev-parse', 'HEAD'), result['commit_id'])
        self.assertEqual(self.call('diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD'), 'a.txt')
        self.assertIn('Coding-Author: codex', self.call('log', '-1', '--format=%B'))
        self.assertIn('Execution-Agent: grok', self.call('log', '-1', '--format=%B'))
        self.assertEqual(self.store.read('t1')['operation']['commit_id'], result['commit_id'])
        self.assertEqual(self.call('ls-remote', 'origin', 'refs/heads/feature/test').split()[0], result['commit_id'])
        self.assertEqual(self.call('ls-remote', 'origin', 'refs/heads/main'), '')

    def test_main_and_duplicate_rejected(self):
        with self.assertRaises(GitAutomationError):
            self.api.create_branch('t1', 'main')
        self.api.create_branch('t1', 'feature/test')
        with self.assertRaises(GitAutomationError):
            self.api.create_branch('t1', 'feature/test')
        self.assertEqual(self.call('rev-parse', 'HEAD'), self.base)

    def test_dirty_switch_rejected_without_losing_file(self):
        (self.repo / 'a.txt').write_text('dirty')
        with self.assertRaises(GitAutomationError):
            self.api.create_branch('t1', 'feature/test')
        self.assertEqual((self.repo / 'a.txt').read_text(), 'dirty')
        self.assertEqual(self.call('branch', '--show-current'), 'main')

    def test_missing_review_and_self_approval_rejected(self):
        self.api.create_branch('t1', 'feature/test')
        with self.assertRaises(FileNotFoundError):
            self.api.commit_and_push('t1', 'feature/test', ['a.txt'], 'feature')
        self.store.write('t1', dict(task_id='t1', branch='feature/test', base_commit=self.base,
                                   author='codex', reviewer='codex', approved=True,
                                   content=self.api.content('t1', ['a.txt'])))
        with self.assertRaises(GitAutomationError):
            self.api.commit_and_push('t1', 'feature/test', ['a.txt'], 'feature')

    def test_changed_review_content_rejected(self):
        self.prepare()
        (self.repo / 'a.txt').write_text('changed after review')
        with self.assertRaises(GitAutomationError):
            self.api.commit_and_push('t1', 'feature/test', ['a.txt'], 'feature')
        self.assertEqual(self.call('rev-parse', 'HEAD'), self.base)

    def test_unrelated_staged_content_preserved(self):
        self.prepare()
        (self.repo / 'other.txt').write_text('other')
        self.call('add', 'other.txt')
        with self.assertRaises(GitAutomationError):
            self.api.commit_and_push('t1', 'feature/test', ['a.txt'], 'feature')
        self.assertEqual(self.call('diff', '--cached', '--name-only'), 'other.txt')

    def test_push_failure_then_retry_does_not_commit_twice(self):
        self.prepare()
        hook = self.remote / 'hooks' / 'pre-receive'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
        first = self.api.commit_and_push('t1', 'feature/test', ['a.txt'], 'feature')
        self.assertEqual(first['push_status'], 'unknown')
        self.assertIsNotNone(first['commit_id'])
        hook.unlink()
        second = self.api.commit_and_push('t1', 'feature/test', ['a.txt'], 'feature')
        self.assertEqual(first['commit_id'], second['commit_id'])
        self.assertEqual(second['push_status'], 'succeeded')
        self.assertEqual(self.call('rev-list', '--count', 'HEAD'), '2')

    def test_timeout_after_push_queries_before_retry(self):
        self.prepare()
        original = self.api._git

        def lost_response(task, *args, **kwargs):
            result = original(task, *args, **kwargs)
            if 'push' in args:
                raise subprocess.TimeoutExpired('git', 1)
            return result

        with patch.object(self.api, '_git', side_effect=lost_response):
            first = self.api.commit_and_push('t1', 'feature/test', ['a.txt'], 'feature')
        self.assertEqual(first['push_status'], 'unknown')
        with patch.object(self.api, '_git', wraps=original) as calls:
            second = self.api.commit_and_push('t1', 'feature/test', ['a.txt'], 'feature')
        self.assertEqual(second['push_status'], 'succeeded')
        self.assertFalse(any('push' in c.args[1:] for c in calls.call_args_list))

    def test_new_binary_and_delete_files(self):
        self.api.create_branch('t1', 'feature/test')
        (self.repo / 'a.txt').unlink()
        (self.repo / 'b.bin').write_bytes(bytes(range(256)))
        files = ['a.txt', 'b.bin']
        self.store.write('t1', dict(task_id='t1', branch='feature/test', base_commit=self.base,
                                   author='codex', reviewer='deepseek-harness', approved=True,
                                   content=self.api.content('t1', files)))
        result = self.api.commit_and_push('t1', 'feature/test', files, 'binary')
        self.assertEqual(result['push_status'], 'succeeded')
        self.assertEqual(self.call('ls-tree', '--name-only', 'HEAD'), 'b.bin')

    def test_hook_rewrites_reviewed_tree_is_not_pushed(self):
        self.prepare()
        hook = self.repo / '.git' / 'hooks' / 'pre-commit'
        hook.write_text('#!/bin/sh\necho unreviewed > a.txt\ngit add a.txt\n')
        hook.chmod(0o755)
        result = self.api.commit_and_push('t1', 'feature/test', ['a.txt'], 'feature')
        self.assertEqual(result['push_status'], 'unknown')
        self.assertEqual(self.call('ls-remote', 'origin'), '')
        self.assertNotEqual(self.call('rev-parse', 'HEAD'), self.base)

    def test_path_and_remote_url_rejected(self):
        for name in ['../outside', '.git/config', 'C:/file', '/absolute', 'a/../a.txt']:
            with self.subTest(name=name), self.assertRaises(GitAutomationError):
                self.api.content('t1', [name])
        self.task = GitTask(self.repo, 'feature/test', self.base, 'https://invalid', 'codex', 'grok')
        with self.assertRaises(GitAutomationError):
            self.api.create_branch('t1', 'feature/test')

    def test_different_push_destination_rejected_before_commit(self):
        self.prepare()
        self.call('remote', 'set-url', '--push', 'origin', str(self.remote.parent / 'other.git'))
        with self.assertRaises(GitAutomationError):
            self.api.commit_and_push('t1', 'feature/test', ['a.txt'], 'feature')
        self.assertEqual(self.call('rev-parse', 'HEAD'), self.base)

    def test_commit_response_lost_returns_commit_and_retry_only_pushes(self):
        self.prepare()
        original = self.api._git

        def lost_response(task, *args, **kwargs):
            result = original(task, *args, **kwargs)
            if args[0] == 'commit':
                raise subprocess.TimeoutExpired('git', 1)
            return result

        with patch.object(self.api, '_git', side_effect=lost_response):
            first = self.api.commit_and_push('t1', 'feature/test', ['a.txt'], 'feature')
        self.assertEqual(first['commit_id'], self.call('rev-parse', 'HEAD'))
        self.assertEqual(first['push_status'], 'not_started')
        second = self.api.commit_and_push('t1', 'feature/test', ['a.txt'], 'feature')
        self.assertEqual(second['push_status'], 'succeeded')
        self.assertEqual(self.call('rev-list', '--count', 'HEAD'), '2')

    def test_precommit_failure_preserves_index_and_never_blindly_retries(self):
        self.prepare()
        hook = self.repo / '.git' / 'hooks' / 'pre-commit'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
        first = self.api.commit_and_push('t1', 'feature/test', ['a.txt'], 'feature')
        self.assertIsNone(first['commit_id'])
        hook.unlink()
        second = self.api.commit_and_push('t1', 'feature/test', ['a.txt'], 'feature')
        self.assertIsNone(second['commit_id'])
        self.assertEqual(self.call('diff', '--cached', '--name-only'), 'a.txt')
        self.assertEqual(self.call('rev-parse', 'HEAD'), self.base)

    def test_literal_pathspec_and_mirror_setting_do_not_broaden_push(self):
        self.api.create_branch('t1', 'feature/test')
        name = '[literal].txt'
        (self.repo / name).write_text('literal')
        self.call('config', 'remote.origin.mirror', 'true')
        self.store.write('t1', dict(task_id='t1', branch='feature/test', base_commit=self.base,
                                   author='codex', reviewer='deepseek-harness', approved=True,
                                   content=self.api.content('t1', [name])))
        result = self.api.commit_and_push('t1', 'feature/test', [name], 'literal')
        self.assertEqual(result['push_status'], 'succeeded')
        self.assertEqual(self.call('ls-remote', 'origin', 'refs/heads/main'), '')


if __name__ == '__main__':
    unittest.main()
