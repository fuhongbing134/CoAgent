import http.client
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from workbench.agents import capability_probe as probes
import web_chat


class FrontendTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js required for frontend regression checks")
    def test_polling_regressions(self):
        script = web_chat.PAGE.split("<script>", 1)[1].split("</script>", 1)[0]
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("test_frontend.cjs"))],
            input=script, text=True, encoding="utf-8", capture_output=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class ProbeTests(unittest.TestCase):
    def test_worker_failure_releases_global_slot(self):
        manager = probes.ProbeManager(lambda directory: (_ for _ in ()).throw(RuntimeError("failed")))
        with patch.object(probes.threading, "Thread") as worker:
            manager.start(Path.cwd())
            job = worker.call_args.kwargs
            job["target"](*job["args"])
        self.assertFalse(manager.running)
        self.assertEqual(manager.snapshot(Path.cwd())["state"], "failed")

    def test_background_deduplication_directory_isolation_and_expiry(self):
        now = [0]
        calls = []
        def collect(directory):
            calls.append(directory)
            return [{"status": "passed"}]
        manager = probes.ProbeManager(collect, lambda: now[0])
        # Cache behavior needs path identities, not real directories or wall-clock waits.
        first, second = Path.cwd() / "probe-first", Path.cwd() / "probe-second"
        with patch.object(probes.threading, "Thread") as worker:
            manager.start(first)
            manager.start(first)
            worker.return_value.start.assert_called_once()
            self.assertEqual(manager.snapshot(first)["state"], "running")
            self.assertEqual(manager.snapshot(second)["state"], "not_tested")
            with self.assertRaises(ValueError):
                manager.start(second)
            job = worker.call_args.kwargs
            job["target"](*job["args"])
            self.assertEqual(manager.snapshot(first)["state"], "completed")
            self.assertEqual(len(calls), 1)
            with self.assertRaises(ValueError):
                manager.start(first)
            now[0] = probes.CACHE_TTL_SECONDS + 1
            self.assertTrue(manager.snapshot(first)["expired"])
            snapshot = manager.snapshot(first)
            snapshot["results"].clear()
            self.assertEqual(len(manager.snapshot(first)["results"]), 1)

    def test_http_errors_are_responses_git_timeout_and_file_cleanup(self):
        def git(directory, *args):
            if args[0] == "status":
                raise subprocess.TimeoutExpired("git", 8)
            return subprocess.CompletedProcess("git", 0, "https://evil.example/repo", "")
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory, patch.object(probes, "run_git", side_effect=git), \
                patch.object(probes, "build_opener") as opener:
            opener.return_value.open.side_effect = HTTPError("https://github.com", 403, "Forbidden", {}, None)
            results = probes.collect(Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertEqual(results[0]["status"], "passed")
        self.assertEqual(results[1]["detail"], "timeout")
        self.assertEqual([r["http_status"] for r in results if r["name"] == "https"], [403, 403])
        self.assertEqual(results[-1]["status"], "skipped")

    def test_remote_url_rewrite_is_not_executed(self):
        responses = [subprocess.CompletedProcess("git", 0, "", ""),
                     subprocess.CompletedProcess("git", 0, "https://github.com/owner/repo.git", ""),
                     subprocess.CompletedProcess("git", 0, "url.evil.insteadof https://github.com", "")]
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory, patch.object(probes, "run_git", side_effect=responses) as git, \
                patch.object(probes, "build_opener") as opener:
            opener.return_value.open.side_effect = TimeoutError()
            results = probes.collect(Path(directory))
        self.assertEqual(results[-1]["status"], "skipped")
        self.assertFalse(any("ls-remote" in call.args for call in git.call_args_list))

    def test_git_is_noninteractive_bounded_and_does_not_use_shell(self):
        with patch.object(probes.subprocess, "run") as run:
            probes.run_git(Path.cwd(), "status")
        kwargs = run.call_args.kwargs
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["timeout"], 8)
        self.assertEqual(kwargs["env"]["GIT_OPTIONAL_LOCKS"], "0")
        self.assertEqual(kwargs["env"]["GCM_INTERACTIVE"], "Never")


class HttpTests(unittest.TestCase):
    def setUp(self):
        web_chat.CLI_AVAILABILITY_CACHE = None

    @classmethod
    def setUpClass(cls):
        cls.server = web_chat.ThreadingHTTPServer(("127.0.0.1", 0), web_chat.ChatHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def request(self, method, path, headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        status, data = response.status, response.read()
        connection.close()
        return status, data

    def test_non_directory_returns_json_error_for_get_and_post(self):
        headers = {"Origin": f"http://127.0.0.1:{self.server.server_port}",
                   "Content-Type": "application/json",
                   "X-Capability-Token": web_chat.CAPABILITY_TOKEN}
        with patch.object(web_chat.ChatHandler, "resolve_working_directory", side_effect=NotADirectoryError("not a directory")):
            for method, path, body in [("GET", "/api/capabilities", None),
                                       ("POST", "/api/capabilities/probe", "{}")]:
                status, data = self.request(method, path, headers, body)
                self.assertEqual(status, 503)
                self.assertIn("error", json.loads(data))

    def test_explicit_broadcast_cannot_silently_lose_grok(self):
        headers = {'Content-Type': 'application/json',
                   'Origin': f'http://127.0.0.1:{self.server.server_port}',
                   'X-Capability-Token': web_chat.CAPABILITY_TOKEN}
        targets = ['codex', 'grok']
        with patch.object(web_chat.ChatHandler, 'resolve_message_recipients', return_value=(targets, dict.fromkeys(targets, 'hello'))), \
             patch.object(web_chat.ChatHandler, 'resolve_grok_command', return_value=None), \
             patch.object(web_chat.ChatHandler, 'resolve_codex_command', return_value=None), \
             patch.object(web_chat.ChatHandler, 'connect_workbench') as db:
            for explicit in (True, False):
                body = {'content': '@all hello'}
                if explicit:
                    body['recipients'] = targets
                status, data = self.request('POST', '/api/messages', headers, json.dumps(body))
                self.assertEqual(status, 503)
                error = json.loads(data)['error']
                self.assertIn('Grok' if explicit else 'Codex', error)
                if explicit:
                    self.assertIn('刷新', error)
            db.assert_not_called()

    def test_cli_discovery_is_cached_until_ttl(self):
        with patch.object(web_chat, "resolve_codex_command", return_value="codex") as codex, \
                patch.object(web_chat, "resolve_dsh_command", return_value=None) as dsh, \
                patch.object(web_chat, "resolve_grok_command", return_value=None) as grok, \
                patch.object(web_chat.time, "monotonic", return_value=10) as clock:
            self.assertTrue(web_chat.cli_availability()["codex_cli_available"])
            clock.return_value = 10 + web_chat.CLI_CACHE_TTL_SECONDS - 1
            web_chat.cli_availability()
            codex.assert_called_once()
            dsh.assert_called_once()
            grok.assert_called_once()
            clock.return_value += 1
            web_chat.cli_availability()
            self.assertEqual(codex.call_count, 2)
            self.assertEqual(dsh.call_count, 2)
            self.assertEqual(grok.call_count, 2)

    def test_probe_requires_origin_token_json_and_valid_payload(self):
        origin = f"http://127.0.0.1:{self.server.server_port}"
        headers = {"Origin": origin, "X-Capability-Token": web_chat.CAPABILITY_TOKEN,
                   "Content-Type": "application/json"}
        with patch.object(web_chat.CAPABILITY_PROBES, "start") as start, \
                patch.object(web_chat.ChatHandler, "resolve_working_directory", return_value=Path.cwd()):
            self.assertEqual(self.request("POST", "/api/capabilities/probe", body="{}")[0], 403)
            self.assertEqual(self.request("POST", "/api/capabilities/probe", {**headers, "Origin":"https://evil.example"}, "{}")[0], 403)
            self.assertEqual(self.request("POST", "/api/capabilities/probe", {**headers, "Content-Type":"text/plain"}, "{}")[0], 415)
            self.assertEqual(self.request("POST", "/api/capabilities/probe", headers, "[]")[0], 400)
            start.assert_not_called()
            self.assertEqual(self.request("POST", "/api/capabilities/probe", headers, "{}")[0], 202)
            start.assert_called_once()

    def test_get_only_reads_cache_and_rejects_foreign_host(self):
        with patch.object(web_chat.CAPABILITY_PROBES, "start") as start, \
                patch.object(web_chat.ChatHandler, "resolve_working_directory", return_value=Path.cwd()), \
                patch.object(web_chat, "resolve_codex_command", return_value=None), \
                patch.object(web_chat, "resolve_dsh_command", return_value=None):
            status, data = self.request("GET", "/api/capabilities")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(data)["probe"]["state"], "not_tested")
            start.assert_not_called()
            self.assertEqual(self.request("GET", "/api/capabilities", {"Host":"evil.example"})[0], 403)
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertNotIn(b"__CAPABILITY_TOKEN__", page)
        self.assertIn(web_chat.CAPABILITY_TOKEN.encode(), page)


if __name__ == "__main__":
    unittest.main()
