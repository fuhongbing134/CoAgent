"""Stage 2 real module/process tests; no replacement runners or mock responses."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from workbench.collaboration.collaboration_entry import entry_prompt, parse_entry_response, route_message
from tests.unit.test_collaboration_foundation import payload


class EntryTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("RUN_LIVE_AGENT_TESTS") == "1", "explicit live CLI opt-in required")
    def test_live_codex_semantic_plan(self):
        configured = os.environ.get("CODEX_CLI") or shutil.which("codex")
        if not configured:
            candidates = list((Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI/Codex/bin").glob("*/codex.exe"))
            configured = str(max(candidates, key=lambda p: p.stat().st_mtime)) if candidates else None
        self.assertTrue(configured, "Codex CLI unavailable")
        with tempfile.TemporaryDirectory() as directory:
            answer = Path(directory) / "answer.txt"
            request = "@DeepSeek Harness 最终由你汇总；@Codex 负责调研报告。现在仅规划，不进行调研或修改文件，不使用工具。"
            result = subprocess.run([configured, "exec", "--sandbox", "read-only", "--skip-git-repo-check",
                "--cd", str(Path(__file__).resolve().parents[2]), "--output-last-message", str(answer),
                entry_prompt(request)], capture_output=True, encoding="utf-8", errors="replace", timeout=180)
            self.assertEqual(result.returncode, 0, (result.stderr or result.stdout)[-2500:])
            _, item = parse_entry_response(answer.read_text(encoding="utf-8"))
            self.assertEqual(item["plan"]["owner_id"], "codex")
            self.assertEqual(item["plan"]["final_responder_id"], "deepseek-harness")

    def test_whole_message_routes_in_child_process(self):
        message = "@Codex 调研，@DeepSeek Harness 最终汇总"
        result = subprocess.run([sys.executable, "-X", "utf8", "-c",
            "import json,sys; from workbench.collaboration.collaboration_entry import route_message; "
            "s=sys.stdin.read(); ids,_=route_message(s); "
            "print(json.dumps((ids,{i:s for i in ids})))"],
            input=message, encoding="utf-8", errors="replace", capture_output=True,
            cwd=Path(__file__).resolve().parents[2], timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        recipients, prompts = json.loads(result.stdout)
        self.assertEqual(recipients, ["codex"])
        self.assertEqual(prompts["codex"], message)

    def test_routing_boundaries(self):
        self.assertEqual(route_message("@DeepSeek Harness 汇总，@Codex 调研"), (["deepseek-harness"], True))
        for text in ("hello", "`@Codex`", "```\n@Codex\n```", r"\@Codex", "mail@codex.com", "https://host/@Codex", "@all @Codex"):
            with self.subTest(text=text):
                self.assertEqual(route_message(text), (["codex", "deepseek-harness", "grok"], False))

    def test_prompt_retains_request_and_contract(self):
        text = "@Codex 查成本，@DeepSeek 查性能"
        prompt = entry_prompt(text)
        self.assertTrue(prompt.endswith(text))
        self.assertIn("agent-collaboration", prompt)
        self.assertIn("continue_work", prompt)

    def test_protocol_envelope(self):
        item = payload()
        item["status"] = "continue_work"
        text = "Readable answer\n```agent-collaboration\n" + json.dumps(item) + "\n```"
        readable, parsed = parse_entry_response(text)
        self.assertEqual(readable, "Readable answer")
        self.assertEqual(parsed, item)
        for bad in ("no protocol", text + "\ntrailing prose", text + "\n@Codex", text + "\n" + text):
            with self.subTest(text=bad), self.assertRaises(ValueError):
                parse_entry_response(bad)

    def test_invitation_and_questions(self):
        item = payload()
        item["status"] = "request_collaboration"
        text = "Plan\n```agent-collaboration\n" + json.dumps(item) + "\n```\n@DeepSeek Harness"
        self.assertEqual(parse_entry_response(text)[1]["status"], "request_collaboration")
        item.pop("plan")
        item["status"] = "waiting_user"
        item["questions"] = ["Who should summarize?"]
        text = "Question\n```agent-collaboration\n" + json.dumps(item) + "\n```"
        self.assertNotIn("plan", parse_entry_response(text)[1])


if __name__ == "__main__":
    unittest.main()
