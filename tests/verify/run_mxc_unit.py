"""Run the unit suite through Codex MXC.

Invoke the installed base python.exe directly from ordinary PowerShell.
The unit suite itself uses the project's .venv by default.
The HTTP tests need loopback access. Use --permission-profile to select
an existing Codex profile that permits it.
"""

import argparse
import os
from pathlib import Path
import subprocess
import tempfile


PROJECT = Path(__file__).resolve().parents[2]

RUN_UNIT_TESTS = """\
import os
import sys
import tempfile
import unittest

project, fixtures, scratch = sys.argv[1:]
sys.path.insert(0, project)
os.environ.update(COAGENT_LINK_FIXTURES=fixtures, TEMP=scratch, TMP=scratch, TMPDIR=scratch)
tempfile.tempdir = scratch
suite = unittest.defaultTestLoader.discover(os.path.join(project, 'tests', 'unit'))
result = unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
"""


def prepare_links(root):
    from _winapi import CreateJunction

    database_links = root / 'instance-hardlink'
    database_links.mkdir()
    original = database_links / 'isolated.db'
    original.touch()
    os.link(original, database_links / 'linked.db')

    workspace_links = root / 'workspace-hardlink'
    (workspace_links / 'sub').mkdir(parents=True)
    note = workspace_links / 'note.txt'
    note.write_text('original', encoding='utf-8')
    os.link(note, workspace_links / 'sub' / 'alias.txt')

    database_directory = root / 'instance-junction' / 'real'
    database_directory.mkdir(parents=True)
    (database_directory / 'isolated.db').touch()
    junction = database_directory.with_name('alias')
    CreateJunction(str(database_directory), str(junction))
    return junction


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', default='codex', help='Codex CLI command or executable path')
    parser.add_argument('--python', type=Path, default=PROJECT / '.venv' / 'Scripts' / 'python.exe')
    parser.add_argument('--permission-profile', default=':workspace')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('MXC verification requires Windows')
    python = args.python.resolve(strict=True)

    with tempfile.TemporaryDirectory(prefix='.mxc-unit-', dir=PROJECT) as directory:
        root = Path(directory)
        scratch = root / 'temp'
        scratch.mkdir()
        fixtures = root / 'links'
        fixtures.mkdir()
        runner = root / 'run_suite.py'
        runner.write_text(RUN_UNIT_TESTS, encoding='utf-8')
        junction = prepare_links(fixtures)
        try:
            return subprocess.run([
                args.codex, '-c', 'windows.sandbox=mxc', 'sandbox',
                '--include-managed-config', '--permission-profile', args.permission_profile,
                '--cd', str(PROJECT), '--', str(python), '-B', '-u', '-X', 'utf8',
                str(runner), str(PROJECT), str(fixtures), str(scratch),
            ], cwd=PROJECT).returncode
        finally:
            junction.rmdir()


if __name__ == '__main__':
    raise SystemExit(main())
