"""Explicitly configured workbench stdio MCP entry; never grants OS permissions."""
import argparse
import json
from pathlib import Path
import sqlite3

from git_automation import GitAutomation, GitAutomationError, GitTask, JsonReviewStore


class HostReviewStore(JsonReviewStore):
    """Approvals are host-owned; execution can only persist operation results."""
    def __init__(self, approvals, results):
        super().__init__(approvals)
        self.results = JsonReviewStore(results)
        if self.directory == self.results.directory:
            raise GitAutomationError('Separate approval and result directories required')

    def read(self, task_id):
        record = super().read(task_id)
        if 'operation' in record:
            raise GitAutomationError('Approval must not contain execution results')
        path = self.results._path(task_id)
        if path.exists():
            saved = self.results.read(task_id)
            # Never attach an old operation to a revised approval.
            import hashlib
            digest = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
            if saved['approval_digest'] != digest:
                raise GitAutomationError('Approval changed after operation; host inspection required')
            record['operation'] = saved['operation']
        return record

    def write(self, task_id, record):
        import hashlib
        approved = super().read(task_id)
        content = {k: v for k, v in record.items() if k != 'operation'}
        if content != approved:
            raise GitAutomationError('Execution cannot change approval')
        digest = hashlib.sha256(json.dumps(approved, sort_keys=True).encode()).hexdigest()
        self.results.write(task_id, {'approval_digest': digest, 'operation': record['operation']})


class WorkbenchGit:
    """Host config binds operation IDs to real workbench task records.

    Configuration/approval ACLs and exclusive workspace scheduling are deployment
    prerequisites. No identity supplied in a tool argument is used as authority.
    """
    def __init__(self, config_path, executor):
        self.config_path = Path(config_path).resolve(strict=True)
        self.config = json.loads(self.config_path.read_text(encoding='utf-8'))
        if executor not in ('codex', 'deepseek-harness', 'grok'):
            raise GitAutomationError('Unknown configured executor')
        self.executor = executor
        self.db = Path(self.config['database']).resolve(strict=True)
        self.reviews = HostReviewStore(self.config['approvals'], self.config['results'])
        self.api = GitAutomation(self.resolve_task, self.reviews)

    def resolve_task(self, task_id):
        # Validate before using task_id for any path or lookup.
        self.reviews._path(task_id)
        binding = self.config['tasks'].get(task_id)
        if not binding or self.executor not in binding['executors']:
            raise GitAutomationError('Task not assigned to this executor')
        connection = sqlite3.connect(self.db.as_uri() + '?mode=ro', uri=True)
        try:
            row = connection.execute('''SELECT t.agent_id, s.working_directory, j.stopped
                FROM collab_tasks t JOIN collab_jobs j ON j.job_id=t.job_id
                JOIN collab_bindings b ON b.job_id=t.job_id
                JOIN workbench_conversation_settings s ON s.conversation_id=b.conversation_id
                WHERE t.job_id=? AND t.task_id=?''', (binding['job_id'], task_id)).fetchone()
        finally:
            connection.close()
        if row is None or row[2]:
            raise GitAutomationError('Task unavailable or stopped')
        directory = Path(row[1]).resolve(strict=True)
        # Bind directory at provisioning so a later conversation switch cannot
        # redirect an approved task into another repository.
        if directory != Path(binding['directory']).resolve(strict=True):
            raise GitAutomationError('Task working directory changed')
        for path in (self.config_path, self.reviews.directory,
                     self.reviews.results.directory):
            if path.is_relative_to(directory):
                raise GitAutomationError('Host state must be outside task repository')
        return GitTask(directory, binding['branch'], binding['base_commit'],
                       binding['remote'], row[0], self.executor)

    def create_branch(self, task_id: str, name: str) -> dict:
        return self.api.create_branch(task_id, name)

    def commit_and_push(self, task_id: str, branch: str, files: list[str], message: str) -> dict:
        return self.api.commit_and_push(task_id, branch, files, message)


def build_server(host):
    from mcp.server.fastmcp import FastMCP
    server = FastMCP('workbench-git')
    server.tool()(host.create_branch)
    server.tool()(host.commit_and_push)
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, help='Host-owned config outside task repository')
    parser.add_argument('--executor', required=True, choices=['codex', 'deepseek-harness', 'grok'])
    args = parser.parse_args()
    build_server(WorkbenchGit(args.config, args.executor)).run(transport='stdio')


if __name__ == '__main__':
    main()
