"""Real SQLite and real loopback HTTP requests; no mocked services."""

import http.client
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from workbench.collaboration.collaboration_actions import ActionStore
from workbench.collaboration.collaboration_http import handle_action_request
from tests.unit.test_collaboration_foundation import payload


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ActionStore(Path(self.temp.name) / "actions.db")
        self.store.create("job", "Original", payload()["plan"])
        self.store.initialize("job")
        self.store.bind("job", "conversation")

    def waiting(self):
        self.store.reserve("job", [("question", "codex", "owner")])
        item = payload()
        item.pop("plan")
        item.update(status="waiting_user", questions=["Scope?"])
        self.store.accept("job", "question", "Scope?", item)

    def test_answer_dedup_and_persistence(self):
        self.waiting()
        def answer(_):
            return self.store.operate("job", "conversation", "answer-1", "answer", "Windows")
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(answer, range(2))), [False, True])
        reopened = ActionStore(self.store.path)
        view = reopened.view("job", "conversation")
        self.assertEqual(view["state"]["phase"], "working")
        self.assertEqual(view["state"]["used"], 2)  # user answer itself is free
        self.assertEqual(len(view["updates"]), 1)
        self.assertEqual(view["events"][0]["kind"], "resume_owner")
        with self.assertRaises(ValueError):
            answer = reopened.operate("job", "conversation", "answer-1", "answer", "Linux")

    def test_early_finish_requires_transport_ack_and_stop_wins(self):
        self.store.reserve("job", [("work", "codex", "owner")])
        self.store.operate("job", "conversation", "finish", "finalize")
        self.assertEqual(self.store.snapshot("job")["phase"], "quiescing")
        self.assertFalse(self.store.accept("job", "work", "late", payload()))
        with self.assertRaises(ValueError):
            self.store.reserve("job", [("final", "deepseek-harness", "final")])
        self.store.operate("job", "conversation", "stop", "stop")
        self.assertFalse(self.store.confirm_quiescent("job", "finish"))
        self.store.confirm_quiescent("job", "stop")
        self.assertEqual(self.store.snapshot("job")["phase"], "stopped")

    def test_finalize_and_limits(self):
        self.store.operate("job", "conversation", "limit", "set_limit", round_limit=10)
        self.assertEqual(self.store.snapshot("job")["round_limit"], 10)
        self.store.operate("job", "conversation", "finish", "finalize")
        self.assertTrue(self.store.confirm_quiescent("job", "finish"))
        self.assertEqual(self.store.snapshot("job")["phase"], "final_ready")
        self.assertFalse(self.store.confirm_quiescent("job", "finish"))
        with self.assertRaises(ValueError):
            self.store.view("job", "other")

    def test_real_http_security_and_update(self):
        store = self.store
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                handle_action_request(self, store, "test-token")
            def send_json(self, status, value):
                body = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_port
            def post(value, authorized=True):
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                try:
                    headers = {"Content-Type": "application/json"}
                    if authorized:
                        headers.update({"Origin": f"http://127.0.0.1:{port}", "X-Capability-Token": "test-token"})
                    connection.request("POST", "/api/collaboration/action", json.dumps(value), headers)
                    response = connection.getresponse()
                    return response.status, json.loads(response.read())
                finally:
                    connection.close()
            request = dict(job_id="job", conversation_id="conversation", operation_id="add", action="append", content="Windows")
            self.assertEqual(post(request, False)[0], 403)
            self.assertEqual(post(request)[0], 200)
            self.assertFalse(post(request)[1]["changed"])
            request["conversation_id"] = "other"
            self.assertEqual(post(request)[0], 400)
            self.assertEqual(post([])[0], 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
