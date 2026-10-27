"""Service contract tests with real SQLite/processes, never fabricated CLI answers."""

import subprocess
import json
import sys
import tempfile
import unittest
from pathlib import Path

from collaboration_service import CollaborationService
from test_collaboration_foundation import payload


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "service.db"

    @staticmethod
    def non_protocol_process(job, agent, role, prompt, **kwargs):
        # Actual unrelated program output verifies rejection, not Agent semantics.
        process = subprocess.run([sys.executable,"-c","import sys; print(sys.version_info.major)"],
                                 capture_output=True,text=True,timeout=10)
        return ("completed" if process.returncode == 0 else "failed",process.stdout)

    @staticmethod
    def no_process_to_cancel(job):
        # These tests have no running child when invoking cancellation events.
        return True

    def service(self):
        return CollaborationService(self.path,self.non_protocol_process,self.no_process_to_cancel)

    def test_append_plan_normalizes_existing_and_new_dependencies(self):
        service = self.service()
        original = payload()["plan"]
        service.store.create("job", "Request", original)
        service.store.initialize("job")
        amended = payload()["plan"]
        del amended["tasks"][0]["depends_on"]
        amended["tasks"].append(dict(task_id="independent", agent_id="codex", instruction="Inspect"))
        service.append_plan("job", amended)
        self.assertEqual(amended["tasks"][0]["depends_on"], [])
        self.assertEqual(amended["tasks"][-1]["depends_on"], [])
        repeated = payload()["plan"]
        repeated["tasks"].append(dict(task_id="independent", agent_id="codex", instruction="Inspect", depends_on=[]))
        service.append_plan("job", repeated)
        with service.store.transaction() as db:
            saved = json.loads(db.execute("SELECT plan FROM collab_jobs WHERE job_id='job'").fetchone()[0])
            count = db.execute("SELECT count(*) FROM collab_tasks WHERE job_id='job'").fetchone()[0]
        self.assertEqual(saved, repeated)
        self.assertEqual(count, 3)

    def test_legacy_entry_fails_without_execution(self):
        service = self.service()
        item = payload()
        item["plan"]["tasks"] = item["plan"]["tasks"][:1]
        item["plan"]["final_responder_id"] = "codex"
        service.register_entry("job","conversation","codex","Original",item,"Original completed output")
        service.drive("job")
        view = service.store.view("job","conversation")
        self.assertEqual(view["state"]["phase"],"failed")
        self.assertEqual(service.material("job")[1][0]["state"], "pending")
        self.assertEqual(view["state"]["used"],1)

    def test_no_plan_question_becomes_persistent_card(self):
        service = self.service()
        item = payload()
        item.pop("plan")
        item.update(status="waiting_user",questions=["Who owns the work?"])
        raw = "Please clarify\n```agent-collaboration\n" + json.dumps(item) + "\n```"
        service.register_entry("job","conversation","codex","Original",item,raw)
        view = service.store.view("job","conversation")
        self.assertEqual(view["state"]["phase"],"waiting_user")
        self.assertEqual(view["plan"]["tasks"][0]["task_id"],"__clarify_entry__")
        service.store.operate("job","conversation","answer","answer","Codex")
        service.drive("job")  # actual non-protocol subprocess must fail, never deliver
        self.assertEqual(service.store.snapshot("job")["phase"],"failed")
        self.assertEqual(service.store.snapshot("job")["used"],2)

    def test_real_process_output_rejected_by_orchestrator(self):
        service = self.service()
        item = payload()
        item.update(status="request_collaboration")
        service.register_entry("job","conversation","codex","Original",item,"Plan\n@DeepSeek Harness")
        service.drive("job")
        self.assertEqual(service.store.snapshot("job")["phase"],"failed")
        self.assertLessEqual(service.store.snapshot("job")["used"],8)

    def test_early_finish_kills_real_process_before_ack(self):
        process = subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"])
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        def cancel(job):
            process.terminate()
            process.wait(timeout=5)
            return True
        service = CollaborationService(self.path,self.non_protocol_process,cancel)
        item = payload()
        item["status"] = "continue_work"
        service.register_entry("job","conversation","codex","Original",item,"Initial material")
        service.store.operate("job","conversation","finish","finalize")
        self.assertEqual(service.store.snapshot("job")["phase"],"quiescing")
        service.events("job")
        self.assertIsNotNone(process.poll())
        self.assertEqual(service.store.snapshot("job")["phase"],"final_ready")

    def test_owner_may_append_review_but_not_change_roles(self):
        service = self.service()
        item = payload()
        item["status"] = "request_collaboration"
        service.register_entry("job","conversation","codex","Original",item,"Plan\n@DeepSeek Harness")
        plan = payload()["plan"]
        plan["tasks"].append(dict(task_id="review-again",agent_id="deepseek-harness",instruction="Review corrected work",depends_on=["review"]))
        service.append_plan("job",plan)
        self.assertEqual(len(service.material("job")[1]),3)
        plan["final_responder_id"] = "codex"
        with self.assertRaises(ValueError):
            service.append_plan("job",plan)


if __name__ == "__main__":
    unittest.main()
