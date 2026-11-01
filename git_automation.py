"""Minimal Git operations for a host-configured, exclusively occupied workspace.

The host supplies task configuration and a reviewer-owned JSON record directory.
Neither task registration nor review approval is exposed as an Agent operation.
This library does not grant OS permissions or install an MCP/HTTP service.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile


class GitAutomationError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitTask:
    directory: Path
    branch: str
    base_commit: str
    remote: str
    author: str
    executor: str


class JsonReviewStore:
    """Host-managed files; keep outside the editable task repository.

    Reviewers/host provision approval records. Execution only adds its result.
    Directory access control is the host's responsibility, not an identity system.
    """
    def __init__(self, directory):
        self.directory = Path(directory).resolve(strict=True)

    def _path(self, task_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', task_id):
            raise GitAutomationError('Invalid task ID')
        return self.directory / (task_id + '.json')

    def read(self, task_id):
        return json.loads(self._path(task_id).read_text(encoding='utf-8'))

    def write(self, task_id, record):
        target = self._path(task_id)
        fd, temporary = tempfile.mkstemp(dir=self.directory, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(record, stream, ensure_ascii=True, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class GitAutomation:
    def __init__(self, resolve_task, reviews, *, executable=None, timeout=60):
        self.resolve_task = resolve_task
        self.reviews = reviews
        self.executable = executable or shutil.which('git')
        if not self.executable:
            raise GitAutomationError('Git unavailable')
        self.timeout = timeout

    def _git(self, task, *args, check=True):
        """The single subprocess boundary, also the future coordination hook."""
        environment = {k: v for k, v in os.environ.items()
                       if not k.upper().startswith('GIT_')}
        environment.update(GIT_TERMINAL_PROMPT='0', GIT_LITERAL_PATHSPECS='1')
        result = subprocess.run([self.executable, '-C', str(task.directory), *args],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=environment, timeout=self.timeout, shell=False)
        if check and result.returncode:
            # Do not return stderr: remote/hook output may contain credentials.
            raise GitAutomationError('Git operation failed: ' + args[0])
        return result

    def _text(self, task, *args):
        return self._git(task, *args).stdout.decode('utf-8').strip()

    def _task(self, task_id):
        task = self.resolve_task(task_id)
        if not isinstance(task, GitTask):
            raise GitAutomationError('Unknown task')
        directory = Path(task.directory).resolve(strict=True)
        if isinstance(self.reviews, JsonReviewStore) and self.reviews.directory.is_relative_to(directory):
            raise GitAutomationError('Review records must be outside task repository')
        if self._text(task, 'rev-parse', '--show-toplevel') != str(directory).replace('\\', '/'):
            raise GitAutomationError('Task must reference repository root')
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', task.base_commit):
            raise GitAutomationError('Task requires immutable base commit')
        if self._text(task, 'rev-parse', task.base_commit + '^{commit}') != task.base_commit:
            raise GitAutomationError('Base unavailable')
        self._branch(task, task.branch)
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', task.remote):
            raise GitAutomationError('Use configured remote name, not URL')
        for value in (task.author, task.executor):
            if not value or any(c in value for c in '\r\n\0'):
                raise GitAutomationError('Invalid contributor identity')
        return task

    def _branch(self, task, branch):
        if not branch.startswith('feature/') or not branch[8:]:
            raise GitAutomationError('Only feature/ branches are supported')
        self._git(task, 'check-ref-format', '--branch', branch)

    def _head(self, task):
        return self._text(task, 'rev-parse', 'HEAD')

    def _ready(self, task):
        for marker in ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD',
                       'rebase-merge', 'rebase-apply'):
            path = Path(self._text(task, 'rev-parse', '--git-path', marker))
            if not path.is_absolute():
                path = Path(task.directory) / path
            if path.exists():
                raise GitAutomationError('Unfinished Git operation')

    def _files(self, task, files):
        if not isinstance(files, list) or not files or len(set(files)) != len(files):
            raise GitAutomationError('Explicit unique file list required')
        root = Path(task.directory).resolve()
        for name in files:
            if (not isinstance(name, str) or not name or '\\' in name or ':' in name
                    or '\0' in name or '\n' in name or '\r' in name):
                raise GitAutomationError('Invalid file path')
            parts = PurePosixPath(name).parts
            if (PurePosixPath(name).is_absolute() or any(p in ('.', '..') or p.lower() == '.git'
                                                       for p in parts)
                    or str(PurePosixPath(name)) != name):
                raise GitAutomationError('Path outside file scope')
            candidate = root / name
            if not candidate.resolve().is_relative_to(root):
                raise GitAutomationError('Path escapes repository')
            if candidate.is_symlink() or candidate.is_dir():
                raise GitAutomationError('Only regular files/deletions supported')
        return sorted(files)

    def content(self, task_id, files):
        """Read-only helper for the reviewer to record exact blob IDs and modes.

        This captures content, NOT an approval. Git clean filters run normally.
        """
        task = self._task(task_id)
        return self._content(task, self._files(task, files))

    def _content(self, task, files):
        result = {}
        for name in files:
            tracked = self._git(task, 'ls-files', '--stage', '-z', '--', name).stdout
            mode = tracked.split(b' ', 1)[0].decode() if tracked else '100644'
            if mode not in ('100644', '100755'):
                raise GitAutomationError('Unsupported file mode or unmerged index')
            path = Path(task.directory) / name
            if not path.exists():
                if not tracked:
                    baseline = self._git(task, 'ls-tree', '-z', task.base_commit, '--', name).stdout
                    if not baseline:
                        raise GitAutomationError('Missing untracked file')
                result[name] = None
            else:
                if not path.is_file():
                    raise GitAutomationError('Only regular files supported')
                filemode = self._git(task, 'config', '--bool', 'core.filemode', check=False).stdout.strip()
                if filemode == b'true':
                    mode = '100755' if path.stat().st_mode & 0o111 else '100644'
                result[name] = [mode, self._text(task, 'hash-object', '--path=' + name, '--', name)]
        return result

    def create_branch(self, task_id, name):
        task = self._task(task_id)
        self._branch(task, name)
        if name != task.branch:
            raise GitAutomationError('Branch differs from task')
        self._ready(task)
        if self._git(task, 'status', '--porcelain', '--untracked-files=all').stdout:
            raise GitAutomationError('Working directory must be clean')
        self._git(task, 'switch', '-c', name, task.base_commit)
        return dict(branch=self._text(task, 'branch', '--show-current'),
                    head=self._head(task), base_commit=task.base_commit)

    def commit_and_push(self, task_id, branch, files, message):
        task = self._task(task_id)
        files = self._files(task, files)
        if branch != task.branch or self._text(task, 'branch', '--show-current') != branch:
            raise GitAutomationError('Current branch differs from task')
        self._ready(task)
        if not isinstance(message, str) or not message.strip() or len(message) > 10000 or '\0' in message:
            raise GitAutomationError('Invalid commit message')
        record = self.reviews.read(task_id)
        if (record.get('task_id') != task_id or record.get('branch') != branch
                or record.get('base_commit') != task.base_commit
                or record.get('author') != task.author or record.get('approved') is not True
                or not record.get('reviewer') or record['reviewer'] == task.author
                or sorted(record.get('content', {})) != files):
            raise GitAutomationError('Independent review required')
        request = hashlib.sha256(json.dumps([branch, files, message, task.remote,
                                             task.author, task.executor]).encode()).hexdigest()
        operation = record.get('operation')
        if operation:
            if operation['request'] != request:
                raise GitAutomationError('Existing operation has different inputs')
            return self._resume(task_id, task, record)
        if self._head(task) != task.base_commit:
            raise GitAutomationError('Reviewed baseline changed')
        if self._git(task, 'diff', '--cached', '--name-only', '-z').stdout:
            raise GitAutomationError('Existing staged content must be preserved')
        if self._content(task, files) != record['content']:
            raise GitAutomationError('Reviewed content changed')
        # Fixed existing remote; no URL accepted from the operation.
        if task.remote not in self._text(task, 'remote').splitlines():
            raise GitAutomationError('Configured remote missing')
        self._remote(task)
        self._git(task, 'add', '--', *files)
        if self._content(task, files) != record['content']:
            raise GitAutomationError('Content changed while staging; index retained')
        staged = self._git(task, 'diff', '--cached', '--name-only', '--no-renames', '-z').stdout
        changed = staged.decode('utf-8').rstrip('\0').split('\0') if staged else []
        if not changed or not set(changed) <= set(files):
            raise GitAutomationError('Unexpected or empty staged scope')
        # Compare index blobs too: filters/hooks must not silently substitute content.
        self._check_index(task, record['content'])
        tree = self._text(task, 'write-tree')
        if self._head(task) != task.base_commit:
            raise GitAutomationError('HEAD changed before commit; index retained')
        text = message + '\n\nCoding-Author: ' + task.author + '\nExecution-Agent: ' + task.executor
        record['operation'] = dict(request=request, tree=tree, commit_id=None,
                                   message=text, push_status='not_started')
        self.reviews.write(task_id, record)  # intent survives lost commit response
        try:
            result = self._git(task, 'commit', '-m', text, check=False)
            failed = result.returncode != 0
        except (subprocess.TimeoutExpired, OSError):
            failed = True
        return self._resume(task_id, task, record, commit_failed=failed)

    def _check_index(self, task, content):
        for name, expected in content.items():
            entry = self._git(task, 'ls-files', '--stage', '-z', '--', name).stdout
            actual = None
            if entry:
                fields = entry.split(b'\t', 1)[0].decode().split()
                if len(fields) != 3 or fields[2] != '0':
                    raise GitAutomationError('Unmerged index')
                actual = fields[:2]
            if actual != expected:
                raise GitAutomationError('Index differs from reviewed content')

    def _resume(self, task_id, task, record, *, commit_failed=False):
        operation = record['operation']
        head = self._head(task)
        expected = operation.get('commit_id')
        if expected and expected != head:
            return dict(commit_id=expected, push_status='unknown', error='HEAD changed; inspect before retry')
        if head == task.base_commit:
            return dict(commit_id=None, push_status='not_started', error='Commit not confirmed; inspect before retry')
        parents = self._text(task, 'rev-list', '--parents', '-n', '1', head).split()
        if (parents != [head, task.base_commit]
                or self._text(task, 'rev-parse', head + '^{tree}') != operation['tree']
                or self._text(task, 'log', '-1', '--format=%B', head) != operation['message'].strip()):
            return dict(commit_id=head, push_status='unknown', error='Commit differs from reviewed result; do not push')
        operation['commit_id'] = head
        try:
            self.reviews.write(task_id, record)
        except OSError:
            return dict(commit_id=head, push_status='not_started', error='Review writeback failed')
        if commit_failed:
            return dict(commit_id=head, push_status='not_started', error='Commit exists; command failed, inspect hooks')
        target = 'refs/heads/' + task.branch
        try:
            self._remote(task)
            remote = self._git(task, 'ls-remote', '--refs', task.remote, target, check=False)
            if remote.returncode:
                raise GitAutomationError('Remote query failed')
            remote_id = remote.stdout.split()[0].decode() if remote.stdout.strip() else None
            if remote_id != head:
                self._git(task, '-c', 'remote.' + task.remote + '.mirror=false',
                          '-c', 'push.followTags=false', 'push', '--no-follow-tags',
                          task.remote, head + ':' + target)
                remote = self._git(task, 'ls-remote', '--refs', task.remote, target)
                if not remote.stdout or remote.stdout.split()[0].decode() != head:
                    raise GitAutomationError('Remote result not confirmed')
            operation['push_status'] = 'succeeded'
        except (GitAutomationError, subprocess.TimeoutExpired, OSError):
            operation['push_status'] = 'unknown'
        try:
            self.reviews.write(task_id, record)
        except OSError:
            return dict(commit_id=head, push_status=operation['push_status'], error='Result writeback failed')
        return dict(commit_id=head, push_status=operation['push_status'])

    def _remote(self, task):
        # Query and push must address the same one configured destination.
        fetch = self._text(task, 'remote', 'get-url', '--all', task.remote).splitlines()
        push = self._text(task, 'remote', 'get-url', '--push', '--all', task.remote).splitlines()
        if len(fetch) != 1 or push != fetch:
            raise GitAutomationError('Distinct/multiple push destinations unsupported')
