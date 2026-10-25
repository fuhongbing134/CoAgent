"""Actual SQLite lifecycle contracts; no mocked CLI or invented Agent replies."""

import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from collaboration_lifecycle import LifecycleStore
from collaboration_batches import execute_batch
from test_collaboration_foundation import payload


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "lifecycle.db"
        self.store = LifecycleStore(self.path)
        self.item = payload()
        self.store.create("job", "Original user request", self.item["plan"])
        self.store.initialize("job")

    def result(self, status="completed"):
        # This is protocol input to a state validator, not a substituted CLI response.
        item = payload()
        item.pop("plan")
        item["status"] = status
        if status == "waiting_user":
            item["questions"] = ["Choose scope"]
        return item

    def test_concurrent_reservation_idempotency_and_restart(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.store.reserve("job", [("owner-1", "codex", "owner")]), range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(LifecycleStore(self.path).snapshot("job")["used"], 2)
        with self.assertRaises(ValueError):
            self.store.reserve("job", [("owner-2", "codex", "owner")])

    def test_budget_preserves_owner_and_final_slots(self):
        self.store.initialize("job", round_limit=100)  # reopen never resets usage or limit
        calls = [(str(i), "deepseek-harness", "collaborator") for i in range(6)]
        with self.assertRaises(ValueError):
            self.store.reserve("job", calls)
        self.assertEqual(self.store.snapshot("job")["used"], 1)
        self.assertTrue(self.store.reserve("job", calls[:5]))
        self.assertEqual(self.store.snapshot("job")["used"], 6)

    def test_middle_final_identity_does_not_deliver(self):
        self.store.reserve("job", [("review", "deepseek-harness", "collaborator")])
        self.assertTrue(self.store.accept("job", "review", "Review recorded", self.result()))
        self.assertEqual(self.store.snapshot("job")["phase"], "working")
        self.assertFalse(self.store.accept("job", "review", "duplicate", self.result()))

    def test_wait_failure_and_stop(self):
        self.store.reserve("job", [("question", "codex", "owner")])
        self.store.accept("job", "question", "Choose scope", self.result("waiting_user"))
        self.assertEqual(self.store.snapshot("job")["phase"], "waiting_user")
        self.assertEqual(self.store.snapshot("job")["used"], 2)
        with self.assertRaises(ValueError):
            self.store.reserve("job", [("next", "codex", "owner")])
        self.store.stop("job")
        with self.assertRaises(ValueError):
            self.store.prepare_final("job", "early finish")
        self.assertEqual(self.store.snapshot("job")["phase"], "stopped")

    def test_invalid_protocol_is_failure_not_completion(self):
        self.store.reserve("job", [("owner", "codex", "owner")])
        self.assertFalse(self.store.accept("job", "owner", "Missing protocol", {}))
        self.assertEqual(self.store.snapshot("job")["phase"], "failed")
        self.assertEqual(self.store.snapshot("job")["used"], 2)

    def test_early_delivery_has_material_and_single_final_call(self):
        self.store.prepare_final("job", "User requested current results")
        material = self.store.delivery_material("job")
        self.assertEqual(material["request"], "Original user request")
        self.assertTrue(all(t["state"] == "cancelled" for t in material["tasks"]))
        with self.assertRaises(ValueError):
            self.store.reserve("job", [("wrong", "codex", "final")])
        self.store.reserve("job", [("final", "deepseek-harness", "final")])
        self.assertFalse(self.store.reserve("job", [("final", "deepseek-harness", "final")]))
        self.store.accept("job", "final", "Existing evidence insufficient; scope remains open.", self.result())
        self.assertEqual(self.store.snapshot("job")["phase"], "completed")
        self.assertEqual(self.store.snapshot("job")["used"], 2)

    def test_real_transport_then_owner_and_final(self):
        self.store.reserve("job", [("research-call", "codex", "collaborator")])
        self.store.open_batch("job", "research", ["research"], [], "codex")
        def runner(task):
            p = subprocess.run([sys.executable, "-c", "import sys; print(sys.version_info.major)"], capture_output=True, text=True, timeout=10)
            return ("completed" if p.returncode == 0 else "failed", p.stdout)
        execute_batch(self.store, "job", ["research"], runner)
        self.store.consume_batch("job", "research")
        self.store.accept("job", "research-call", "Transport finished", self.result())
        self.store.reserve("job", [("review-call", "deepseek-harness", "collaborator")])
        self.store.open_batch("job", "review", ["review"], ["deepseek-harness"], "codex")
        execute_batch(self.store, "job", ["review"], runner)
        self.store.consume_batch("job", "review")
        self.store.accept("job", "review-call", "Transport finished", self.result())
        self.store.reserve("job", [("owner", "codex", "owner")])
        self.store.accept("job", "owner", "Task work finished", self.result())
        self.assertEqual(self.store.snapshot("job")["phase"], "final_ready")
        self.assertEqual(self.store.snapshot("job")["used"], 4)

    def test_recovery_never_replays_reserved_call(self):
        self.store.reserve("job", [("owner", "codex", "owner")])
        self.store.interrupt_running()
        self.assertEqual(self.store.snapshot("job")["phase"], "interrupted")
        self.assertFalse(self.store.accept("job", "owner", "late", self.result()))


if __name__ == "__main__":
    unittest.main()
