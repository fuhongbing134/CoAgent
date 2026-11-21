"""HTTP routes for the local workbench."""

import json
import secrets
import sqlite3
import threading
from urllib.parse import parse_qs, urlparse

from workbench.adapters.grok_adapter import requested_recipients
from workbench.collaboration.collaboration_entry import route_message
from workbench.collaboration.collaboration_http import handle_action_request
from workbench.web.page import PAGE
from workbench.web.transport import WorkbenchRequestHandler


class ChatHandler(WorkbenchRequestHandler):

    def capability_host_allowed(self) -> bool:
        port = self.server.server_port
        return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def capability_snapshot(self, conversation_id: str) -> dict:
        available = self.cli_availability()
        return {
            "probe": self.CAPABILITY_PROBES.snapshot(self.resolve_working_directory(conversation_id)),
            "agent_configuration": [
                {"id": "codex", "name": "Codex", "configuration": f"--sandbox {self.CODEX_SANDBOX}",
                 "cli_found": available["codex_cli_available"]},
                {"id": "deepseek-harness", "name": "DeepSeek Harness", "configuration": f"--profile {self.DSH_PROFILE}; 未显式传入 sandbox 参数，其他策略未知",
                 "cli_found": available["dsh_cli_available"]},
                {"id": "grok", "name": "Grok", "configuration": "普通：auto，工具可能被拒绝、不等待人工批准；最终交付：dontAsk + deny全部工具（非Windows OS沙箱）",
                 "cli_found": available["grok_cli_available"]},
            ],
        }

    def do_capability_probe(self) -> None:
        host = self.headers.get("Host", "")
        if (not self.capability_host_allowed()
                or self.headers.get("Origin") != "http://" + host
                or not secrets.compare_digest(self.headers.get("X-Capability-Token", ""), self.CAPABILITY_TOKEN)):
            self.send_json(403, {"error": "Capability probe requires same-origin authorization."})
            return
        if self.headers.get_content_type() != "application/json":
            self.send_json(415, {"error": "Expected application/json"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096 or self.headers.get("Transfer-Encoding"):
                raise ValueError("Invalid request length")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            conversation_id = payload.get("conversation_id", "default") if isinstance(payload, dict) else None
            if not isinstance(conversation_id, str) or not 0 < len(conversation_id) <= 200:
                raise ValueError("Invalid conversation_id")
            directory = self.resolve_working_directory(conversation_id)
        except (ValueError, UnicodeError) as error:
            self.send_json(400, {"error": str(error)})
            return
        except (sqlite3.Error, OSError):
            self.send_json(503, {"error": "Working directory unavailable"})
            return
        try:
            self.CAPABILITY_PROBES.start(directory)
        except ValueError as error:
            self.send_json(409, {"error": str(error)})
            return
        self.send_json(202, {"status": "accepted"})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/collaboration/tasks":
                if not self.capability_host_allowed():
                    self.send_json(403, {"error": "Invalid Host"})
                    return
                conversation = parse_qs(parsed.query).get("conversation_id", ["default"])[0]
                store = self.collaboration_service().store
                with store.transaction() as db:
                    ids = [r[0] for r in db.execute("SELECT job_id FROM collab_bindings WHERE conversation_id=? ORDER BY rowid DESC LIMIT 20", (conversation,))]
                self.send_json(200, {"tasks": [store.view(job_id, conversation) for job_id in ids]})
                return

            if parsed.path in {"/", "/api/capabilities"} and not self.capability_host_allowed():
                self.send_json(403, {"error": "Invalid Host"})
                return
            if parsed.path == "/api/capabilities":
                conversation_id = parse_qs(parsed.query).get("conversation_id", ["default"])[0]
                if not 0 < len(conversation_id) <= 200:
                    self.send_json(400, {"error": "Invalid conversation_id"})
                    return
                self.send_json(200, self.capability_snapshot(conversation_id))
                return
            if parsed.path == "/":
                self.send_bytes(200, "text/html; charset=utf-8", PAGE.replace("__CAPABILITY_TOKEN__", self.CAPABILITY_TOKEN).encode("utf-8"))
                return
            if parsed.path == "/api/agents":
                self.send_json(200, {
                    "agents": self.fetch_agents(),
                    **self.cli_availability(),
                })
                return
            if parsed.path == "/api/conversations":
                self.send_json(200, {"conversations": self.fetch_conversations()})
                return
            if parsed.path == "/api/settings":
                params = parse_qs(parsed.query)
                conversation_id = params.get("conversation_id", ["default"])[0]
                if not conversation_id or len(conversation_id) > 200:
                    self.send_json(400, {"error": "conversation_id 不能为空且不能超过 200 个字符"})
                    return
                self.send_json(200, {
                    "conversation_id": conversation_id,
                    "working_directory": self.get_working_directory_text(conversation_id),
                })
                return
            if parsed.path == "/api/runs":
                self.send_json(200, {"runs": self.list_active_runs()})
                return
            if parsed.path == "/api/messages":
                params = parse_qs(parsed.query)
                conversation_id = params.get("conversation_id", ["default"])[0]
                if len(conversation_id) > 200:
                    self.send_json(400, {"error": "conversation_id 太长"})
                    return
                try:
                    limit = int(params.get("limit", ["200"])[0])
                except ValueError:
                    limit = 200
                limit = max(1, min(limit, self.MAX_MESSAGES))
                self.send_json(200, {
                    "conversation_id": conversation_id,
                    "messages": self.fetch_messages(conversation_id, limit),
                })
                return
            self.send_json(404, {"error": "Not found"})
        except (sqlite3.Error, OSError) as error:
            self.send_json(503, {"error": str(error)})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/collaboration/action":
            service = self.collaboration_service()
            handle_action_request(self, service.store, self.CAPABILITY_TOKEN, service.action_changed)
            return
        if parsed.path == "/api/capabilities/probe":
            self.do_capability_probe()
            return
        if parsed.path not in {"/api/messages", "/api/stop", "/api/settings"}:
            self.send_json(404, {"error": "Not found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(400, {"error": "无效的 Content-Length"})
            return
        if content_length < 1 or content_length > 100_000:
            self.send_json(413, {"error": "请求内容为空或过大"})
            return

        try:
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {"error": "请求必须是有效的 UTF-8 JSON"})
            return
        if not isinstance(payload, dict):
            self.send_json(400, {"error": "请求内容必须是 JSON 对象"})
            return

        if parsed.path == "/api/settings":
            conversation_id = payload.get("conversation_id", "default")
            working_directory = payload.get("working_directory")
            if not isinstance(conversation_id, str) or not conversation_id or len(conversation_id) > 200:
                self.send_json(400, {"error": "conversation_id 必须是 1 到 200 个字符的字符串"})
                return
            if not isinstance(working_directory, str) or len(working_directory) > 32768:
                self.send_json(400, {"error": "working_directory 必须是绝对路径字符串，且长度不能超过 32768 个字符"})
                return
            try:
                normalized_directory = self.normalize_working_directory(working_directory)
            except ValueError as error:
                self.send_json(400, {"error": str(error)})
                return
            with self.ACTIVE_RUNS_LOCK:
                if any(
                    run["conversation_id"] == conversation_id
                    for run in self.ACTIVE_RUNS.values()
                ):
                    self.send_json(409, {"error": "该对话有 Agent 正在运行，请等任务完成或停止后再更改工作目录。"})
                    return
                try:
                    changed = self.save_working_directory(conversation_id, normalized_directory)
                except sqlite3.Error as error:
                    self.send_json(503, {"error": str(error)})
                    return
            self.send_json(200, {
                "conversation_id": conversation_id,
                "working_directory": normalized_directory,
                "changed": changed,
            })
            return

        if parsed.path == "/api/stop":
            try:
                service = self.collaboration_service()
                with service.store.transaction() as db:
                    bindings = list(db.execute("SELECT job_id,conversation_id FROM collab_bindings"))
                for job_id, conversation in bindings:
                    with self.ACTIVE_RUNS_LOCK:
                        matching = payload.get("message_id") in self.COLLAB_MESSAGES.get(job_id,set())
                    if payload.get("all") is True or matching:
                        if service.store.snapshot(job_id)["phase"] not in {"completed","stopped"}:
                            service.store.operate(job_id,conversation,secrets.token_hex(16),"stop")
                            service.action_changed(job_id)
                if payload.get("all") is True:
                    stopped = self.stop_all_active_runs()
                    self.send_json(200, {"stopped": stopped})
                    return
                message_id = payload.get("message_id")
                if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id < 1:
                    self.send_json(400, {"error": "message_id 必须是正整数，或指定 all=true"})
                    return
                if not self.stop_active_run(message_id):
                    self.send_json(409, {"error": "该 Agent 已完成或已经停止"})
                    return
                self.send_json(200, {"stopped": 1, "message_id": message_id})
            except sqlite3.Error as error:
                self.send_json(503, {"error": str(error)})
            return

        content = payload.get("content")
        conversation_id = payload.get("conversation_id", "default")
        if not isinstance(content, str) or not isinstance(conversation_id, str):
            self.send_json(400, {"error": "content 和 conversation_id 必须是字符串"})
            return
        content = content.strip()
        if not content:
            self.send_json(400, {"error": "消息内容不能为空"})
            return
        if len(content) > self.MAX_MESSAGE_LENGTH:
            self.send_json(400, {"error": f"消息不能超过 {self.MAX_MESSAGE_LENGTH} 个字符"})
            return
        if not conversation_id or len(conversation_id) > 200:
            self.send_json(400, {"error": "conversation_id 不能为空且不能超过 200 个字符"})
            return
        recipients, agent_prompts = self.resolve_message_recipients(content)
        try:
            recipients = requested_recipients(recipients, payload.get('recipients'), route_message(content)[1])
            agent_prompts = {key: agent_prompts[key] for key in recipients}
        except ValueError as error:
            self.send_json(400, {'error': str(error)})
            return
        if not recipients or not agent_prompts:
            self.send_json(400, {"error": "请在 Agent 名称后输入要发送的消息"})
            return

        if "grok" in recipients and not self.resolve_grok_command():
            if len(recipients) == 1 or payload.get('recipients') is not None:
                self.send_json(503, {
                    "error": "Grok CLI 不可用，请刷新可用名单后重试；也可设置 GROK_CLI 为 grok.exe 的完整路径。"
                })
                return
            # A missing Grok never blocks a broadcast to the other Agents.
            recipients = [agent_id for agent_id in recipients if agent_id != "grok"]
            agent_prompts.pop("grok", None)

        if "codex" in recipients and not self.resolve_codex_command():
            self.send_json(503, {
                "error": "找不到 Codex CLI。请确认已安装 Codex；也可以设置 CODEX_CLI 为 codex.exe 的完整路径。"
            })
            return
        if "deepseek-harness" in recipients:
            try:
                if not any(agent["agent_id"] == "deepseek-harness" for agent in self.fetch_agents()):
                    self.send_json(409, {"error": "DeepSeek Harness 尚未登记到 agent_bridge。"})
                    return
            except sqlite3.Error as error:
                self.send_json(503, {"error": str(error)})
                return
            if not self.resolve_dsh_command():
                self.send_json(503, {
                    "error": "找不到 dsh CLI。请在 DeepSeek Harness 桌面端的“Manage dsh Command”中安装命令，重新打开 PowerShell 并重启工作台。"
                })
                return

        with self.ACTIVE_RUNS_LOCK:
            active_run = next(
                (
                    run for run in self.ACTIVE_RUNS.values()
                    if run["conversation_id"] == conversation_id
                    and run["agent_id"] in recipients
                ),
                None,
            )
        connection = self.connect_workbench(timeout=10)
        try:
            connection.execute("BEGIN IMMEDIATE")
            recipient_placeholders = ", ".join("?" for _ in recipients)
            pending = connection.execute(
                f"""SELECT sender_id FROM workbench_messages
                    WHERE conversation_id = ? AND sender_id IN ({recipient_placeholders})
                      AND status = 'pending'
                    LIMIT 1""",
                (conversation_id, *recipients),
            ).fetchone()
            if pending:
                connection.rollback()
                label = self.AGENT_LABELS.get(pending[0], "DeepSeek Harness")
                self.send_json(409, {
                    "error": label + " 正在处理这段对话。请等待它完成，或只 @ 另一个 Agent。"
                })
                return
            if active_run:
                connection.rollback()
                label = self.AGENT_LABELS.get(active_run["agent_id"], "DeepSeek Harness")
                self.send_json(409, {"error": label + " 的上一条任务尚未完全结束，请稍后重试。"})
                return

            created_at = self.utc_now()
            user_recipient_id = "all" if len(recipients) > 1 else recipients[0]
            user_cursor = connection.execute(
                """INSERT INTO workbench_messages
                   (conversation_id, sender_id, recipient_id, content, created_at, status)
                   VALUES (?, 'user', ?, ?, ?, 'completed')""",
                (conversation_id, user_recipient_id, content, created_at),
            )
            assistant_message_ids = {}
            for agent_id in recipients:
                assistant_cursor = connection.execute(
                    """INSERT INTO workbench_messages
                       (conversation_id, sender_id, recipient_id, content, created_at, status)
                       VALUES (?, ?, 'user', '', ?, 'pending')""",
                    (conversation_id, agent_id, created_at),
                )
                assistant_message_ids[agent_id] = assistant_cursor.lastrowid
            connection.commit()
            user_message_id = user_cursor.lastrowid
        except sqlite3.Error as error:
            connection.rollback()
            self.send_json(503, {"error": str(error)})
            return
        finally:
            connection.close()

        for agent_id in recipients:
            self.register_active_run(assistant_message_ids[agent_id], conversation_id, agent_id)

        self.send_json(202, {
            "conversation_id": conversation_id,
            "user_message_id": user_message_id,
            "assistant_message_ids": assistant_message_ids,
            "recipients": recipients,
            "status": "accepted",
        })
        for agent_id in recipients:
            threading.Thread(
                target=self.run_agent_task,
                args=(agent_id, conversation_id, agent_prompts[agent_id], assistant_message_ids[agent_id], user_message_id),
                daemon=True,
            ).start()

    def log_message(self, format: str, *args: object) -> None:
        print("[%s] %s" % (self.log_date_time_string(), format % args))
