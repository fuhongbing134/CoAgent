"""Executable foundation tests: real files/processes, no mocks or simulated CLI."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_registry import AgentRegistry, DEFAULT_AGENTS, default_registry
from collaboration_protocol import HANDOFF_FIELDS, loads_payload, validate_payload, validate_plan


def payload():
    return dict(protocol_version=1, status="completed", questions=[],
                handoff={key: "none" for key in HANDOFF_FIELDS},
                plan=dict(owner_id="codex", final_responder_id="deepseek-harness", tasks=[
                    dict(task_id="research", agent_id="codex", instruction="Research", depends_on=[]),
                    dict(task_id="review", agent_id="deepseek-harness", instruction="Review", depends_on=["research"])]))


class FoundationTests(unittest.TestCase):
    def test_default_aliases(self):
        registry = default_registry()
        self.assertEqual(registry.resolve("@DeepSeek Harness").agent_id, "deepseek-harness")
        self.assertEqual(registry.resolve("OPENAI").agent_id, "codex")
        self.assertEqual(len(registry.enabled_agents()), 3)

    def test_registry_rejections(self):
        for field, value in [("enabled", 1), ("adapter", "unknown"), ("agent_id", "bad id"), ("aliases", [""])]:
            records = copy.deepcopy(DEFAULT_AGENTS)
            records[0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                AgentRegistry(records)
        records = copy.deepcopy(DEFAULT_AGENTS)
        records[1]["aliases"].append("OPENAI")
        with self.assertRaises(ValueError):
            AgentRegistry(records)
        records = copy.deepcopy(DEFAULT_AGENTS)
        records[0]["enabled"] = False
        with self.assertRaises(ValueError):
            AgentRegistry(records).require("codex")

    def test_actual_file_and_child_process(self):
        root = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agents.json"
            records = copy.deepcopy(DEFAULT_AGENTS)
            records.append(dict(agent_id="reviewer", name="Reviewer", aliases=["review"], adapter="codex", enabled=True))
            path.write_text(json.dumps(records), encoding="utf-8")
            result = subprocess.run([sys.executable, "-c",
                "import sys; from agent_registry import AgentRegistry; "
                "r=AgentRegistry.from_file(sys.argv[1]); print(r.resolve('review').agent_id)", str(path)],
                cwd=root, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "reviewer")

    def test_registry_optional_defaults_and_unknown_fields(self):
        records = copy.deepcopy(DEFAULT_AGENTS)
        records[0].pop("enabled")
        self.assertTrue(AgentRegistry(records).require("codex").enabled)
        self.assertNotIn("enabled", records[0])
        records[0]["enabeld"] = False
        with self.assertRaisesRegex(ValueError, "unknown=.*enabeld"):
            AgentRegistry(records)
        records = copy.deepcopy(DEFAULT_AGENTS)
        records[0].pop("adapter")
        with self.assertRaisesRegex(ValueError, "missing=.*adapter"):
            AgentRegistry(records)

    def test_resolve_errors_retain_input(self):
        for name in ("Nobody", "@@Codex"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, name):
                default_registry().resolve(name)
        with self.assertRaises(ValueError):
            default_registry().resolve(None)

    def test_valid_payload_and_copy(self):
        original = payload()
        result = loads_payload(json.dumps(original), require_plan=True)
        self.assertEqual(result, original)
        result["questions"].append("changed")
        self.assertFalse(original["questions"])
        original.pop("plan")
        validate_payload(original)
        with self.assertRaises(ValueError):
            validate_payload(original, require_plan=True)

    def test_omitted_dependencies_normalize_without_mutating_payload(self):
        explicit = payload()
        omitted = copy.deepcopy(explicit)
        del omitted["plan"]["tasks"][0]["depends_on"]
        before = copy.deepcopy(omitted)
        result = validate_payload(omitted)
        self.assertEqual(result, validate_payload(explicit))
        self.assertEqual(omitted, before)
        result["plan"]["tasks"][0]["depends_on"].append("changed")
        self.assertEqual(omitted, before)
        self.assertEqual(loads_payload(json.dumps(before))["plan"], explicit["plan"])

    def test_invalid_dependencies_still_rejected_without_input_mutation(self):
        # Owner parsing checks structure; complete graphs are checked after merge.
        for deps in (None, "research", {}, ["missing"], ["review"],
                     ["research", "research"]):
            item = payload()
            del item["plan"]["tasks"][0]["depends_on"]
            item["plan"]["tasks"][1]["depends_on"] = deps
            before = copy.deepcopy(item)
            with self.subTest(deps=deps), self.assertRaises(ValueError):
                validate_plan(validate_payload(item)["plan"], default_registry())
            self.assertEqual(item, before)
        item = payload()
        item["plan"]["tasks"][0]["depends_on"] = ["review"]
        with self.assertRaisesRegex(ValueError, "cyclic"):
            validate_plan(validate_payload(item)["plan"], default_registry())
        item = payload()
        del item["plan"]["tasks"][0]["depends_on"]
        item["plan"]["tasks"][0]["unknown"] = True
        with self.assertRaises(ValueError):
            validate_payload(item)

    def test_statuses_and_context(self):
        item = payload()
        item["status"] = "waiting_user"
        item["questions"] = ["Which target?"]
        validate_payload(item)
        item["questions"] = []
        with self.assertRaises(ValueError):
            validate_payload(item)
        item["status"] = "blocked"
        item["blocked_reason"] = "Need access"
        validate_payload(item)
        item = payload()
        item["status"] = "request_collaboration"
        validate_payload(item, invitation_ids=["deepseek-harness"])
        for kwargs in ({}, {"invitation_ids": ["unknown"]},
                       {"invitation_ids": ["codex"], "role": "collaborator"},
                       {"invitation_ids": ["codex"], "phase": "final_delivery"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                validate_payload(item, **kwargs)

    def test_invalid_graphs(self):
        for deps in (["missing"], ["review"], ["research", "research"]):
            item = payload()
            item["plan"]["tasks"][1]["depends_on"] = deps
            with self.subTest(deps=deps), self.assertRaises(ValueError):
                validate_plan(validate_payload(item)["plan"], default_registry())
        item = payload()
        item["plan"]["tasks"][0]["depends_on"] = ["review"]
        with self.assertRaises(ValueError):
            validate_plan(validate_payload(item)["plan"], default_registry())

    def test_bad_fields_and_json(self):
        for key, value in [("protocol_version", True), ("status", "failed"), ("questions", "bad"), ("handoff", {})]:
            item = payload()
            item[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_payload(item)
        with self.assertRaises(ValueError):
            loads_payload('{"status":"completed","status":"blocked"}')
        with self.assertRaises(ValueError):
            loads_payload("not JSON")


if __name__ == "__main__":
    unittest.main()
