import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from workbench.adapters import workspace_files
from tests.fixtures.link_fixtures import prepared_link_fixture


class WorkspaceFilesTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(dir=Path.cwd()))).resolve()
        (self.root / 'sub').mkdir()

    def call(self, operation, path, text=''):
        return workspace_files.operate(self.root, operation, {'path': path, 'text': text})

    def test_nested_file_lifecycle(self):
        target = self.root / 'sub' / 'note.txt'
        self.call('create_file', 'sub/note.txt', 'first version')
        with self.assertRaises(FileExistsError):
            self.call('create_file', 'sub/note.txt', 'replacement')
        self.assertEqual(target.read_text(encoding='utf-8'), 'first version')
        self.call('edit_file', 'sub/note.txt', '短')
        self.assertEqual(target.read_text(encoding='utf-8'), '短')
        self.call('delete_file', 'sub/note.txt')
        self.assertEqual(list(target.parent.iterdir()), [])

    def test_restricted_names(self):
        for path in ('../note.txt', str(self.root / 'note.txt'), '.GIT/config',
                     'sub/CON.txt', 'sub/dir./note.txt', 'sub/note.txt:stream'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.call('create_file', path)
        self.assertEqual(list((self.root / 'sub').iterdir()), [])

    def test_git_worktree_configuration_is_a_regular_file(self):
        self.call('create_file', '.gitignore', 'ignored.txt')
        self.assertEqual((self.root / '.gitignore').read_text(encoding='utf-8'), 'ignored.txt')

    def test_hard_link_is_rejected(self):
        prepared = prepared_link_fixture('workspace-hardlink')
        root = prepared if prepared is not None else self.root
        original = root / 'note.txt'
        alias = root / 'sub' / 'alias.txt'
        if prepared is None:
            original.write_text('original', encoding='utf-8')
            os.link(original, alias)
        self.assertTrue(original.samefile(alias))
        self.assertEqual(original.stat().st_nlink, 2)
        self.assertEqual(alias.stat().st_nlink, 2)
        before = original.read_bytes()
        for operation in ('edit_file', 'delete_file'):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(ValueError, 'one link'):
                    workspace_files.operate(root, operation, {'path': 'sub/alias.txt', 'text': 'replacement'})
                self.assertEqual(original.read_bytes(), before)
                self.assertTrue(alias.samefile(original))
                self.assertEqual(alias.stat().st_nlink, 2)

    @unittest.skipUnless(os.name == 'nt', 'Windows directory sharing semantics')
    def test_open_workspace_directory_cannot_be_renamed(self):
        from unittest.mock import patch

        from workbench.adapters import workspace_files_windows

        child = self.root / 'sub'
        renamed = self.root / 'renamed'
        check_handle = workspace_files_windows.check_handle

        def check_directory(handle, directory=False):
            path = check_handle(handle, directory)
            if path == child:
                with self.assertRaises(PermissionError):
                    child.rename(renamed)
            return path

        with patch.object(workspace_files_windows, 'check_handle', side_effect=check_directory) as check:
            self.call('create_file', 'sub/note.txt', 'content')
        self.assertEqual(check.call_count, 3)
        child.rename(renamed)
        self.assertEqual((renamed / 'note.txt').read_text(encoding='utf-8'), 'content')

    def test_stdio_continues_after_malformed_input(self):
        requests = ['{', '[]', json.dumps({
            'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
        }), json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})]
        result = subprocess.run(
            [sys.executable, workspace_files.__file__, str(self.root)],
            input='\n'.join(requests) + '\n', encoding='utf-8', capture_output=True,
            check=True, timeout=10,
        )
        invalid_json, invalid_request, initialized, listed = map(json.loads, result.stdout.splitlines())
        self.assertEqual(invalid_json['error']['code'], -32700)
        self.assertEqual(invalid_request['error']['code'], -32600)
        self.assertEqual(initialized['id'], 1)
        self.assertEqual({tool['name'] for tool in listed['result']['tools']},
                         {'create_file', 'edit_file', 'delete_file'})
