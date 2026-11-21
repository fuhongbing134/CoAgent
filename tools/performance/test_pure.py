"""Author checks: no web_chat import, service or managed child execution."""
import json
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core import ALLOCATIONS, Budget, appointments, classify, summarize
from fixture import output, reply
from runner import Driver, audit
from types import SimpleNamespace
from unittest.mock import patch
from workbench.collaboration.collaboration_entry import parse_entry_response
from workbench.adapters.grok_adapter import parse_output


class PureChecks(unittest.TestCase):
    def test_capped_schedule(self):
        due, horizon = appointments(.75, 1.25)
        self.assertEqual(len(due), 24)
        self.assertAlmostEqual(due[-1], 13.8)
        self.assertAlmostEqual(horizon, 14.4)

    def test_window_boundary(self):
        self.assertEqual(appointments(1, .5)[0], list(range(0, 20, 2)))
        self.assertEqual(appointments(1, .5)[1], 20)
        self.assertEqual(appointments(100, .5)[0], [0])

    def test_bad_schedule(self):
        for d in (0, -1, math.inf, math.nan):
            with self.assertRaises(ValueError):
                appointments(d, 1)

    def test_request_allocation(self):
        self.assertEqual(sum(ALLOCATIONS.values()), 450)
        budget = Budget()
        for _ in range(6):
            budget.request("trial")
        with self.assertRaises(RuntimeError):
            budget.request("trial")
        self.assertEqual(budget.counts["trial"], 6)
        with self.assertRaises(RuntimeError):
            budget.request("unknown")

    def test_process_limit(self):
        budget = Budget()
        for _ in range(6):
            budget.start()
        with self.assertRaises(RuntimeError):
            budget.start()
        self.assertEqual(budget.starts, 6)

    def test_job_limit(self):
        budget = Budget()
        for _ in range(5):
            budget.start("job")
            budget.finish()
        with self.assertRaises(RuntimeError):
            budget.start("job")
        self.assertEqual(budget.starts, 5)

    def test_total_process_limit(self):
        budget = Budget()
        for _ in range(470):
            budget.start()
            budget.finish()
        with self.assertRaises(RuntimeError):
            budget.start()

    def test_deadline(self):
        with self.assertRaises(RuntimeError):
            Budget(seconds=-1).request("trial")

    def test_terminal_and_censored_accounting(self):
        records = [dict(classification="completed", send=0, seen=1),
                   dict(classification="completed", send=0, seen=9),
                   dict(classification="unfinished", send=0),
                   dict(classification="rejected", send=0, ack=2)]
        result = summarize(records, 0, 5)
        self.assertEqual(result["samples"], 1)
        self.assertEqual(result["completed_per_second"], .2)
        self.assertEqual(sum(result["counts"].values()), 4)
        self.assertEqual(classify(409), "rejected")
        self.assertEqual(classify(202), "unfinished")
        self.assertEqual(classify(503), "http_error")

    def test_actual_window_and_late_dimensions(self):
        rows = [dict(classification=k, send=1, seen=8) for k in ("completed", "failed", "rejected")]
        rows += [dict(classification="completed", send=1, seen=2), dict(classification="unfinished", send=3)]
        result = summarize(rows, 0, 5, 9)
        self.assertEqual(result["counts"], dict(unfinished=4, completed=1))
        self.assertEqual(result["late_counts"], dict(completed=1, failed=1, rejected=1))
        self.assertEqual(result["final_counts"]["completed"], 2)
        self.assertEqual(result["denominator_seconds"], 4)
        for concurrency in (1, 3, 6):
            labelled = [dict(r, concurrency=concurrency) for r in rows]
            self.assertEqual(summarize(labelled, 0, 5, 9)["completed_per_second"], .25)
        self.assertIsNone(summarize([], 0, 60, 2)["completed_per_second"])
        self.assertFalse(summarize([dict(classification="driver_error", send=None)], 0, 60, 2)["computable"])

    def test_wrapper_exception_ownership(self):
        for evidence in ("confirmed", "not_started", "unknown", "missing"):
            budget = Budget()
            budget.start()
            try:
                raise OSError("simulated wrapper failure")
            except OSError:
                budget.settle(evidence)
            self.assertEqual(budget.calls_active, 0)
            uncertain = evidence in ("unknown", "missing")
            self.assertEqual(budget.active, int(uncertain))
            self.assertEqual(budget.unconfirmed, int(uncertain))
            self.assertEqual(budget.stopped.is_set(), uncertain)

    def test_budget_failure_records_reservation(self):
        h = SimpleNamespace(budget=Budget(allocations={"open": 0}), modes={})
        driver = Driver(h, 1)
        driver.one("open", due=123)
        self.assertEqual(len(driver.records), 1)
        self.assertEqual(driver.records[0]["due"], 123)
        self.assertEqual(driver.records[0]["classification"], "driver_error")
        self.assertIsNone(driver.records[0]["send"])

    def test_open_future_exception_is_recorded_and_raised(self):
        h = SimpleNamespace(budget=Budget(), modes={})
        driver = Driver(h, 1)
        with patch("runner.appointments", return_value=([0, 0], 0)), patch.object(driver, "one", side_effect=RuntimeError("worker failed")):
            with self.assertRaisesRegex(RuntimeError, "open workers failed"):
                driver.open_window(1, 1)
        self.assertEqual(len(driver.records), 2)
        self.assertTrue(all(r["classification"] == "driver_error" for r in driver.records))

    def test_collaboration_smoke_caps(self):
        budget = Budget(seconds=60, allocations={"C4": 1}, starts=5, active=1)
        budget.request("C4")
        with self.assertRaises(RuntimeError):
            budget.request("C4")
        for _ in range(5):
            budget.start("job")
            budget.settle("confirmed")
        with self.assertRaises(RuntimeError):
            budget.start("job")

    def test_real_entry_parser(self):
        for agent in ("codex", "deepseek-harness", "grok"):
            payload = parse_entry_response(reply(agent, "entry", "marker"))[1]
            self.assertEqual(payload["status"], "continue_work")
            self.assertEqual(len(payload["plan"]["tasks"]), 2)
            for role in ("collaborator", "owner", "final"):
                payload = parse_entry_response(reply(agent, role, "marker"), role=role,
                    phase="final_delivery" if role == "final" else "collaboration")[1]
                self.assertEqual(payload["status"], "completed")
                self.assertNotIn("plan", payload)
        with self.assertRaises(ValueError):
            parse_entry_response(reply("codex", "entry", "marker", bad=True))

    def test_adapter_wire_contracts(self):
        text = reply("grok", "entry", "marker")
        answer, sid, errors, _ = parse_output(output("grok", "fixture-1", text))
        self.assertEqual((answer, sid, errors), (text, "fixture-1", []))
        dsh = [json.loads(line) for line in output("deepseek-harness", "s1", text).splitlines()]
        self.assertEqual(dsh[0]["sessionId"], "s1")
        self.assertEqual(dsh[1]["text"], text)
        self.assertEqual(json.loads(output("codex", "s1", text))["thread_id"], "s1")
        self.assertLess(len(text.encode())+len(output("codex", "s1", text).encode()), 4096)

    def test_audit_rejects_missing_run_and_exit(self):
        report = dict(records=[dict(group="trial", http=202, message=1, classification="unfinished", conversation="test")],
                      runs=[], events=[dict(event="launch_requested", message=1, role="direct", agent="codex", session="s1")])
        failures = audit(report)["failures"]
        self.assertTrue(any("unique run" in f for f in failures))
        self.assertTrue(any("unresolved" in f for f in failures))
        self.assertTrue(any("exit not confirmed" in f for f in failures))
        dropped = dict(records=[dict(group="open", classification="generator_dropped")], runs=[], events=[])
        self.assertEqual(audit(dropped)["failures"], [])
        self.assertTrue(audit(dict(summaries=[dict(metrics=dict(computable=False))]))["failures"])

    def test_no_product_runtime_import(self):
        self.assertNotIn("web_chat", sys.modules)
        self.assertNotIn("agent_bridge", sys.modules)


if __name__ == "__main__":
    unittest.main()
