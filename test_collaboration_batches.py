"""Real SQLite, threads and OS child processes; no Agent response substitution."""

import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from collaboration_batches import BatchStore, execute_batch
from collaboration_invitation import terminal_invitation


def plan():
    return dict(owner_id="codex", final_responder_id="codex", tasks=[
        dict(task_id="a", agent_id="codex", instruction="Inspect Python runtime", depends_on=[]),
        dict(task_id="b", agent_id="deepseek-harness", instruction="Inspect Python runtime", depends_on=[]),
        dict(task_id="c", agent_id="codex", instruction="Combine", depends_on=["a", "b"])])


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "tasks.db"
        self.store = BatchStore(self.path)
        self.store.create("job", "Original request", plan())

    def open(self):
        return self.store.open_batch("job", "first", ["a", "b"], ["deepseek-harness"], "codex")

    def test_omitted_dependencies_create_and_deduplicate(self):
        omitted = plan()
        del omitted["tasks"][0]["depends_on"]
        del omitted["tasks"][1]["depends_on"]
        self.assertTrue(self.store.create("defaulted", "Request", omitted))
        self.assertEqual(omitted, plan())
        self.assertFalse(self.store.create("defaulted", "Request", plan()))
        omitted_again = plan()
        del omitted_again["tasks"][0]["depends_on"]
        self.assertFalse(self.store.create("defaulted", "Request", omitted_again))
        with self.store.transaction() as db:
            saved = json.loads(db.execute("SELECT plan FROM collab_jobs WHERE job_id='defaulted'").fetchone()[0])
            deps = db.execute("SELECT deps FROM collab_tasks WHERE job_id='defaulted' AND task_id='a'").fetchone()[0]
        self.assertEqual(saved, plan())
        self.assertEqual(json.loads(deps), [])

    def test_invitation_boundaries(self):
        self.assertEqual(terminal_invitation("Prose @Codex\n@DeepSeek Harness @Codex @Codex\n"), ("deepseek-harness", "codex"))
        for text in ("prose @Codex", "> @Codex", "```\n@Codex", "~~~\n@Codex", "    @Codex", r"\@Codex"):
            with self.subTest(text=text):
                self.assertEqual(terminal_invitation(text), ())
        for text in ("@Nobody", "@Codex please review", "@Codex @"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                terminal_invitation(text)

    def test_dedup_and_dependency_validation(self):
        self.assertFalse(self.store.create("job", "Original request", plan()))
        self.assertTrue(self.open())
        self.assertFalse(self.open())
        with self.assertRaises(ValueError):
            self.store.open_batch("job", "early", ["c"], [], "codex")
        with self.assertRaises(ValueError):
            self.store.open_batch("job", "other", ["b"], ["deepseek-harness"], "deepseek-harness")

    def test_real_process_batch_and_persistence(self):
        self.open()
        # Exercises real transport plumbing, not a fake LLM or semantic assertion.
        def runner(task):
            process = subprocess.run([sys.executable, "-c", "import sys; print(sys.version_info.major)"],
                                     capture_output=True, text=True, timeout=10)
            return ("completed" if process.returncode == 0 else "failed", process.stdout)
        execute_batch(self.store, "job", ["a", "b"], runner, parallel=True)
        reopened = BatchStore(self.path)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: reopened.consume_batch("job", "first"), range(2)))
        delivered = [r for r in results if r is not None]
        self.assertEqual(len(delivered), 1)
        self.assertEqual([r["state"] for r in delivered[0]], ["completed", "completed"])
        self.assertTrue(all(r["result"].strip() == str(sys.version_info.major) for r in delivered[0]))
        self.assertTrue(reopened.open_batch("job", "second", ["c"], [], "codex"))

    def test_wait_all_failure_and_late_completion(self):
        self.open()
        self.store.claim("job", "a")
        self.store.finish("job", "a", "completed", "actual task transport finished")
        self.assertIsNone(self.store.consume_batch("job", "first"))
        self.store.claim("job", "b")
        self.store.finish("job", "b", "timeout", "transport deadline")
        self.assertFalse(self.store.finish("job", "b", "completed", "late"))
        results = self.store.consume_batch("job", "first")
        self.assertEqual(results[1]["state"], "timeout")
        with self.assertRaises(ValueError):
            self.store.open_batch("job", "second", ["c"], [], "codex")

    def test_stop_blocks_claim_and_join(self):
        self.open()
        self.store.claim("job", "a")
        self.store.stop("job")
        self.assertIsNone(self.store.claim("job", "b"))
        self.assertFalse(self.store.finish("job", "a", "completed", "late"))
        self.assertIsNone(self.store.consume_batch("job", "first"))


if __name__ == "__main__":
    unittest.main()
