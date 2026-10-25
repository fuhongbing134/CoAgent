"""Persistent batch primitives. No implicit plan execution or Agent startup."""

import json
import sqlite3
from contextlib import closing, contextmanager
from concurrent.futures import ThreadPoolExecutor

from agent_registry import default_registry
from collaboration_protocol import validate_plan

TERMINAL = {"completed", "failed", "timeout", "cancelled"}


class BatchStore:
    def __init__(self, path, registry=None):
        self.path = str(path)
        self.registry = registry or default_registry()
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS collab_jobs (
                    job_id TEXT PRIMARY KEY, request TEXT NOT NULL, plan TEXT NOT NULL,
                    stopped INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS collab_batches (
                    job_id TEXT NOT NULL, batch_id TEXT NOT NULL, consumed INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(job_id,batch_id));
                CREATE TABLE IF NOT EXISTS collab_tasks (
                    job_id TEXT NOT NULL, task_id TEXT NOT NULL, agent_id TEXT NOT NULL,
                    instruction TEXT NOT NULL, deps TEXT NOT NULL, batch_id TEXT,
                    state TEXT NOT NULL DEFAULT 'pending', result TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(job_id,task_id));
            """)

    @contextmanager
    def transaction(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            with db:
                db.execute("BEGIN IMMEDIATE")
                yield db

    def create(self, job_id, request, plan):
        if not isinstance(job_id, str) or not job_id.strip() or not isinstance(request, str) or not request.strip():
            raise ValueError("nonempty job_id and request required")
        validate_plan(plan, self.registry)
        encoded = json.dumps(plan, sort_keys=True, ensure_ascii=False)
        with self.transaction() as db:
            previous = db.execute("SELECT request,plan FROM collab_jobs WHERE job_id=?", (job_id,)).fetchone()
            if previous:
                if tuple(previous) != (request, encoded):
                    raise ValueError("job ID reused with different content")
                return False
            db.execute("INSERT INTO collab_jobs(job_id,request,plan) VALUES (?,?,?)", (job_id, request, encoded))
            db.executemany("INSERT INTO collab_tasks(job_id,task_id,agent_id,instruction,deps) VALUES (?,?,?,?,?)",
                           [(job_id, t["task_id"], t["agent_id"], t["instruction"], json.dumps(t["depends_on"])) for t in plan["tasks"]])
            return True

    def _job(self, db, job_id):
        row = db.execute("SELECT * FROM collab_jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("unknown job")
        return row

    def open_batch(self, job_id, batch_id, task_ids, invitation_ids, caller_id):
        """Explicitly select ready work; a plan alone never launches a task."""
        if not isinstance(batch_id, str) or not batch_id.strip() or not task_ids or len(set(task_ids)) != len(task_ids):
            raise ValueError("batch/task IDs required and must be unique")
        with self.transaction() as db:
            job = self._job(db, job_id)
            plan = json.loads(job["plan"])
            if job["stopped"] or caller_id != plan["owner_id"]:
                raise ValueError("stopped job or caller is not owner")
            rows = {r["task_id"]: dict(r) for r in db.execute("SELECT * FROM collab_tasks WHERE job_id=?", (job_id,))}
            if any(t not in rows for t in task_ids):
                raise ValueError("unknown task")
            selected = [rows[t] for t in task_ids]
            expected = {r["agent_id"] for r in selected if r["agent_id"] != caller_id}
            if expected != set(invitation_ids):
                raise ValueError("selected tasks must match invitations (owner's own work needs no self-invitation)")
            exists = db.execute("SELECT 1 FROM collab_batches WHERE job_id=? AND batch_id=?", (job_id, batch_id)).fetchone()
            if exists:
                actual = {r[0] for r in db.execute("SELECT task_id FROM collab_tasks WHERE job_id=? AND batch_id=?", (job_id, batch_id))}
                if actual != set(task_ids):
                    raise ValueError("batch ID reused")
                return False
            if any(r["state"] != "pending" or any(rows[d]["state"] != "completed" for d in json.loads(r["deps"])) for r in selected):
                raise ValueError("task already dispatched or dependencies not completed")
            if db.execute("SELECT 1 FROM collab_tasks WHERE job_id=? AND state IN ('queued','running')", (job_id,)).fetchone():
                raise ValueError("previous batch still active")
            db.execute("INSERT INTO collab_batches(job_id,batch_id) VALUES (?,?)", (job_id, batch_id))
            db.executemany("UPDATE collab_tasks SET state='queued',batch_id=? WHERE job_id=? AND task_id=?",
                           [(batch_id, job_id, t) for t in task_ids])
            return True

    def claim(self, job_id, task_id):
        with self.transaction() as db:
            if self._job(db, job_id)["stopped"]:
                return None
            row = db.execute("SELECT * FROM collab_tasks WHERE job_id=? AND task_id=? AND state='queued'", (job_id, task_id)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE collab_tasks SET state='running' WHERE job_id=? AND task_id=?", (job_id, task_id))
            return dict(row)

    def finish(self, job_id, task_id, state, result):
        if state not in TERMINAL or not isinstance(result, str):
            raise ValueError("invalid terminal result")
        with self.transaction() as db:
            if self._job(db, job_id)["stopped"]:
                return False
            cursor = db.execute("UPDATE collab_tasks SET state=?,result=? WHERE job_id=? AND task_id=? AND state='running'",
                                (state, result, job_id, task_id))
            return cursor.rowcount == 1

    def consume_batch(self, job_id, batch_id):
        """Atomically return all results once, only after the entire batch terminates."""
        with self.transaction() as db:
            if self._job(db, job_id)["stopped"]:
                return None
            batch = db.execute("SELECT consumed FROM collab_batches WHERE job_id=? AND batch_id=?", (job_id,batch_id)).fetchone()
            if batch is None:
                raise ValueError("unknown batch")
            rows = list(db.execute("SELECT * FROM collab_tasks WHERE job_id=? AND batch_id=? ORDER BY task_id", (job_id,batch_id)))
            if batch[0] or any(r["state"] not in TERMINAL for r in rows):
                return None
            db.execute("UPDATE collab_batches SET consumed=1 WHERE job_id=? AND batch_id=?", (job_id,batch_id))
            return [dict(r) for r in rows]

    def stop(self, job_id):
        with self.transaction() as db:
            self._job(db, job_id)
            db.execute("UPDATE collab_jobs SET stopped=1 WHERE job_id=?", (job_id,))
            db.execute("UPDATE collab_tasks SET state='cancelled' WHERE job_id=? AND state IN ('pending','queued','running')", (job_id,))


def execute_batch(store, job_id, task_ids, runner, *, parallel=False):
    """Runner returns (terminal_state, text). Caller must ensure isolation before parallel=True.

    Transport cancellation/process termination belongs to the runner. This function
    never resumes an owner or retries a crashed call automatically.
    """
    def execute(task_id):
        task = store.claim(job_id, task_id)
        if task is None:
            return
        try:
            state, result = runner(task)
            store.finish(job_id, task_id, state, result)
        except Exception as error:
            store.finish(job_id, task_id, "failed", str(error))
    with ThreadPoolExecutor(max_workers=len(task_ids) if parallel else 1) as pool:
        list(pool.map(execute, task_ids))
