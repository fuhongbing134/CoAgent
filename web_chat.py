"""Local multi-agent workbench backed by the agent_bridge SQLite database."""

import argparse
import hashlib
import uuid
from dataclasses import dataclass
from functools import wraps
import json
import logging
import os
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


from agent_registry import default_registry
from grok_adapter import command as grok_command_args, parse_output as grok_parse_output, diagnostic as grok_diagnostic, requested_recipients
from database_schema import ensure_schema, require_current
from migration_barrier import MigrationBarrier
from service_instance import ServiceInstanceLock
from capability_probe import ProbeManager
from collaboration_entry import route_message, entry_prompt, parse_entry_response
from collaboration_batches import BatchStore
from collaboration_lifecycle import LifecycleStore
from collaboration_actions import ActionStore
from collaboration_http import handle_action_request
from collaboration_service import CollaborationService, InvocationResult
from git_feature_workflow import GitFeatureWorkflow
from git_feature_http import handle_git_action
from execution_runtime import Execution, ExecutionAdmissionError, format_execution_error
from execution_inspection import InspectionQueryError, read_occupancy_snapshot
from execution_tracking import DomainBusy, invalidate_sessions


CODEX_SANDBOX = "workspace-write"
DSH_PROFILE = "headless"
CAPABILITY_TOKEN = secrets.token_urlsafe(32)
CAPABILITY_PROBES = ProbeManager()
CLI_CACHE_TTL_SECONDS = 300
CLI_AVAILABILITY_LOCK = threading.Lock()
CLI_AVAILABILITY_CACHE = None


def cli_availability() -> dict:
    """Cache discovery for display only; execution still resolves the actual CLI."""
    global CLI_AVAILABILITY_CACHE
    with CLI_AVAILABILITY_LOCK:
        now = time.monotonic()
        if CLI_AVAILABILITY_CACHE is None or now - CLI_AVAILABILITY_CACHE[0] >= CLI_CACHE_TTL_SECONDS:
            CLI_AVAILABILITY_CACHE = (now, {
                "codex_cli_available": resolve_codex_command() is not None,
                "dsh_cli_available": resolve_dsh_command() is not None,
                "grok_cli_available": resolve_grok_command() is not None,
            })
        return dict(CLI_AVAILABILITY_CACHE[1])

PROJECT_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("AGENT_BRIDGE_DB", PROJECT_DIR / "agent_bridge.db")).expanduser()
DSH_AUTH_PATCH = Path(os.environ.get("DSH_HEADLESS_PATCH", PROJECT_DIR / "dsh-headless-auth.patch.yml")).expanduser()
MAX_MESSAGES = 1000
MAX_MESSAGE_LENGTH = 20_000
AGENT_IDS = ("codex", "deepseek-harness", "grok")
DSH_SESSION_KEY = "deepseek-harness-headless"
GROK_SESSION_KEY = "grok-headless"
AGENT_LABELS = {"codex": "Codex", "deepseek-harness": "DeepSeek Harness", "grok": "Grok"}


def _env_positive_int(name: str, default: int) -> int:
    """Read a positive-integer environment variable, warning and falling back on invalid input."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        print(f"[警告] 环境变量 {name}={raw!r} 不是整数，使用默认值 {default}。", file=sys.stderr)
        return default
    if value < 1:
        print(f"[警告] 环境变量 {name}={raw!r} 必须是正整数，使用默认值 {default}。", file=sys.stderr)
        return default
    return value


# 上下文注入配置：仅控制「保留的正文」长度与条数，省略提示不占用这些上限。
AGENT_CONTEXT_MAX_MESSAGES = _env_positive_int("AGENT_CONTEXT_MAX_MESSAGES", 30)
AGENT_CONTEXT_MAX_MESSAGE_CHARS = _env_positive_int("AGENT_CONTEXT_MAX_MESSAGE_CHARS", 10000)
AGENT_CODEX_TIMEOUT_SECONDS = _env_positive_int("AGENT_CODEX_TIMEOUT_SECONDS", 1200)
AGENT_DEEPSEEK_TIMEOUT_SECONDS = _env_positive_int("AGENT_DEEPSEEK_TIMEOUT_SECONDS", 1200)
AGENT_GROK_TIMEOUT_SECONDS = _env_positive_int("AGENT_GROK_TIMEOUT_SECONDS", 1200)
MENTION_PATTERNS = {
    "codex": re.compile(r"(?<![A-Za-z0-9_@\\])@(?:codex|openai)(?![A-Za-z0-9_]|\.[A-Za-z])", re.IGNORECASE),
    "deepseek-harness": re.compile(
        r"(?<![A-Za-z0-9_@\\])@(?:deepseek\s+harness|deepseekharness|deepseek)(?![A-Za-z0-9_]|\.[A-Za-z])",
        re.IGNORECASE,
    ),
    "grok": re.compile(r"(?<![A-Za-z0-9_@\\])@(?:grok-build|grok|xai)(?![A-Za-z0-9_]|\.[A-Za-z])", re.IGNORECASE),
}
ALL_AGENTS_MENTION = re.compile(
    r"(?<![A-Za-z0-9_@\\])@(?:all|everyone|所有|全部)(?![A-Za-z0-9_]|\.[A-Za-z])",
    re.IGNORECASE,
)
CODE_SPAN_PATTERN = re.compile(r"```[\s\S]*?```|`[^`\r\n]*`")
ACTIVE_RUNS_LOCK = threading.RLock()
# Lock discipline: never nest the registry gate with per-run locks. Execution
# order is state_lock -> Execution.lock -> MigrationBarrier -> BEGIN IMMEDIATE.
# Settings alone uses registry -> barrier -> SQLite with timeout=0. No DB holder
# acquires registry/run locks; waits for Job exit hold none of these locks.
ACTIVE_RUNS: dict[int, dict] = {}
SERVICE_INSTANCE_ID = None
# No automatic production cutover: independently reviewed backend evidence is required.
# Resolver: (agent_id, cwd, executable, environment) -> immutable resolved configuration.
SESSION_CONFIGURATION_RESOLVER = None
COLLAB_SERVICE = None
COLLAB_SERVICE_LOCK = threading.Lock()
COLLAB_AGENT_LOCKS = {agent_id: threading.Lock() for agent_id in AGENT_IDS}
COLLAB_MESSAGES = {}


def collaboration_service():
    global COLLAB_SERVICE
    with COLLAB_SERVICE_LOCK:
        if COLLAB_SERVICE is None:
            COLLAB_SERVICE = CollaborationService(DB_PATH, invoke_collaboration, cancel_collaboration,
                                                  finalize_message=finalize_message,
                                                  git_factory=lambda store: GitFeatureWorkflow(store, git_job_directory))
        return COLLAB_SERVICE


def git_job_directory(job_id):
    with closing(connect_workbench()) as db:
        row = db.execute('SELECT conversation_id FROM collab_bindings WHERE job_id=?', (job_id,)).fetchone()
    if not row:
        raise ValueError('Missing conversation for Git feature')
    return resolve_working_directory(row[0])


def cancel_collaboration(job_id):
    with ACTIVE_RUNS_LOCK:
        runs = [ACTIVE_RUNS[i] for i in COLLAB_MESSAGES.get(job_id, ()) if i in ACTIVE_RUNS]
    for run in runs:
        stop_active_run(run['message_id'])
    deadline = time.monotonic() + 12
    for run in runs:
        execution = run.get('execution')
        if execution and (not execution.done.wait(max(0, deadline - time.monotonic()))
                          or not execution.released):
            return False
    # Also include runs whose registry entry disappeared, e.g. a release failure.
    with closing(connect_workbench()) as db:
        return not db.execute("SELECT 1 FROM execution_runs WHERE source_job_id=? AND state IN ('pending','running','quiescing')", (job_id,)).fetchone()


def invoke_collaboration(job_id, agent_id, role, prompt, *, call_id, entry_mode=False):
    service = collaboration_service()
    with service.store.transaction() as db:
        call = db.execute("SELECT state,text FROM collab_calls WHERE job_id=? AND call_id=? AND agent_id=? AND role=?",
                          (job_id,call_id,agent_id,role)).fetchone()
        if not call:
            raise ValueError('Missing real collaboration call')
        if call['state'] != 'reserved':
            return InvocationResult(call['state'], call['text'] or '', True)
        conversation = db.execute("SELECT conversation_id FROM collab_bindings WHERE job_id=?", (job_id,)).fetchone()[0]
    with COLLAB_AGENT_LOCKS[agent_id]:
        # Recheck after serial-agent admission: another invocation may have
        # finished while this caller waited, before its call row was accepted.
        with closing(connect_workbench()) as db:
            previous = db.execute("""SELECT r.state,r.source_message_id FROM execution_runs r
                WHERE r.source_job_id=? AND r.source_call_id=? AND r.agent_id=?
                LIMIT 1""", (job_id,call_id,agent_id)).fetchone()
            if previous:
                # Recover the original reply for reception, not a placeholder
                # that would be parsed as a new invalid protocol response.
                message = db.execute('SELECT content FROM workbench_messages WHERE id=?',
                                     (previous[1],)).fetchone()
        if previous:
            state = previous[0]
            text = message[0] if message and message[0] else '重复调用，不重放；原回复不可用，需人工核查。'
            return InvocationResult(state, text,
                                    state in {'completed','failed','cancelled'}, previous[1])
        if service.store.snapshot(job_id)['phase'] in {'stopped', 'quiescing'}:
            return InvocationResult('cancelled', 'Cancelled before CLI start', False)
        with closing(connect_workbench()) as db, db:
            message_id = db.execute("INSERT INTO workbench_messages(conversation_id,sender_id,recipient_id,content,created_at,status) VALUES (?,?,'user','',?,'pending')",
                                    (conversation,agent_id,utc_now())).lastrowid
        register_active_run(message_id,conversation,agent_id)
        active_run(message_id)['collaboration'] = (job_id, call_id)
        with ACTIVE_RUNS_LOCK:
            COLLAB_MESSAGES.setdefault(job_id,set()).add(message_id)
        allowed = False
        reply = WorkerResult('cancelled', 'Cancelled before CLI start')
        previous_final = getattr(COLLAB_CONTEXT, 'final_delivery', False)
        try:
            if service.store.snapshot(job_id)['phase'] not in {'working', 'final_delivery'}:
                update_workbench_message(message_id, '已停止。', 'cancelled')
            else:
                begin_execution(message_id, source_kind='collaboration', job_id=job_id, call_id=call_id)
                COLLAB_CONTEXT.final_delivery = role == 'final'
                worker = agent_worker(agent_id)
                reply = worker(conversation,prompt,message_id,message_id)
                if not isinstance(reply, WorkerResult):
                    raise RuntimeError('Worker returned no structured result')
        except Exception as error:
            active_run(message_id)['failed'] = True
            reply = WorkerResult('failed', '执行失败：' + format_execution_error(error)[:2000])
            update_workbench_message(message_id, reply.text, 'failed')
        finally:
            COLLAB_CONTEXT.final_delivery = previous_final
            allowed = finish_execution(message_id, outcome=reply.status)
            unregister_active_run(message_id)
            with ACTIVE_RUNS_LOCK:
                COLLAB_MESSAGES.get(job_id,set()).discard(message_id)
        return InvocationResult(reply.status, reply.text, allowed, message_id)


COLLAB_CONTEXT = threading.local()


class AgentRunCancelled(Exception):
    """Raised when the user stops an active Agent CLI process."""


def clean_mention_segment(content: str) -> str:
    for pattern in MENTION_PATTERNS.values():
        content = pattern.sub("", content)
    content = ALL_AGENTS_MENTION.sub("", content)
    content = content.replace(r"\@", "@")
    return re.sub(r"[ \t]+([,，。.!?！？:：;；])", r"\1", content)


def clean_message_prompt(content: str) -> str:
    """Remove routing mentions outside code while preserving code spans."""
    parts = []
    cursor = 0
    for match in CODE_SPAN_PATTERN.finditer(content):
        parts.append(clean_mention_segment(content[cursor:match.start()]))
        parts.append(match.group())
        cursor = match.end()
    parts.append(clean_mention_segment(content[cursor:]))
    return "".join(parts).strip()


def resolve_message_recipients(content: str) -> tuple[list[str], dict[str, str]]:
    """Preserve the whole request; the first mention selects only the entry."""
    recipients, _collaboration = route_message(content)
    return recipients, {agent_id: content for agent_id in recipients}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _WorkbenchConnection(sqlite3.Connection):
    def close(self):
        try:
            super().close()
        finally:
            permit = getattr(self, '_permit', None)
            if permit is not None:
                self._permit = None
                permit.__exit__(None, None, None)


def connect_workbench(timeout=10):
    permit = MigrationBarrier(DB_PATH)
    permit.__enter__()
    db = None
    try:
        db = sqlite3.connect(Path(DB_PATH).resolve().as_uri() + '?mode=rw', uri=True, timeout=timeout, factory=_WorkbenchConnection)
        db._permit = permit
        require_current(db)
        return db
    except BaseException:
        if db is not None:
            db.close()
        else:
            permit.__exit__(None, None, None)
        raise


def initialize_workbench_tables() -> None:
    ensure_schema(DB_PATH)
    connection = connect_workbench(timeout=10)
    try:
        with connection:
            connection.execute("""UPDATE workbench_messages SET content=CASE WHEN EXISTS (
                SELECT 1 FROM execution_runs r WHERE r.source_message_id=workbench_messages.id
                AND (r.state IN ('pending','running','quiescing')
                    OR EXISTS (SELECT 1 FROM execution_domains d
                               WHERE d.owner_run_id=r.run_id AND d.state!='idle')
                    OR EXISTS (SELECT 1 FROM execution_sessions s WHERE s.owner_run_id=r.run_id))
                ) THEN ? ELSE ? END, status='cancelled' WHERE status='pending'""",
                ('工作台曾中断；此任务未完成收尾，旧执行退出情况未知，相关占用仍保留。',
                 '工作台曾中断；此消息已取消，旧执行退出情况未知，未确认关联占用。'))
    finally:
        connection.close()


PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agent 工作台</title>
  <style>
    :root { color-scheme: dark; --bg:#101216; --panel:#171a20; --panel2:#1d2129; --line:#2b303a; --text:#edf0f5; --muted:#98a1b1; --accent:#a8c7fa; --green:#7bd8b0; }
    * { box-sizing:border-box; }
    html,body { width:100%;height:100%;overflow:hidden; }
    body { margin:0; min-height:0; background:radial-gradient(ellipse at 65% -20%,#263348 0,transparent 42%),var(--bg); color:var(--text); font:14px/1.5 "Segoe UI",system-ui,sans-serif; }
    button,select { color:inherit; font:inherit; }
    .app { width:100%;height:100vh;height:100dvh;min-height:0;overflow:hidden;display:grid;grid-template-columns:290px minmax(0,1fr); }
    aside { min-height:0;height:100vh;height:100dvh;overflow-y:auto;border-right:1px solid var(--line);background:rgba(19,22,28,.88);padding:24px 17px; }
    .brand { display:flex; align-items:center; gap:12px; padding:2px 8px 25px; }
    .logo { width:36px;height:36px;border-radius:12px;display:grid;place-items:center;background:linear-gradient(145deg,#a8c7fa,#b8ead5);color:#101216;font-weight:800;font-size:17px; }
    .brand-name { font-size:16px;font-weight:700;letter-spacing:.1px; }
    .brand-sub { color:var(--muted);font-size:11px;margin-top:2px; }
    .avatar { flex:none;width:32px;height:32px;border-radius:10px;display:grid;place-items:center;font-weight:750;color:#12151b;background:#a8c7fa; }
    .avatar.deepseek { background:#b8ead5; }
    .avatar.grok { background:#d9c2f5; }
    .side-note { color:#788292;font-size:11px;line-height:1.6;padding:14px 8px; }
    main { width:100%;height:100vh;height:100dvh;min-width:0;min-height:0;overflow:hidden;display:grid;grid-template-rows:auto minmax(0,1fr) auto; }
    .topbar { min-height:68px;position:relative;z-index:2;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:12px clamp(18px,4vw,48px);gap:16px;background:rgba(16,18,22,.88);backdrop-filter:blur(12px); }
    .title { font-size:16px;font-weight:700; }
    .subtitle { color:var(--muted);font-size:12px;margin-top:3px; }
    .tools { display:flex;align-items:center;gap:12px; }
    select { background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:8px 30px 8px 10px;outline:none; }
    .refresh { display:flex;align-items:center;gap:8px;color:var(--muted);font-size:11px;white-space:nowrap; }
    .refresh-copy { display:grid;gap:1px; }
    .refresh-frequency { color:#707b8a;font-size:10px; }
    .dot { width:7px;height:7px;background:var(--green);border-radius:50%;box-shadow:0 0 9px #7bd8b077; }
    .stop-button { border:1px solid #78454a;border-radius:8px;padding:6px 10px;background:#392326;color:#ffc2c2;font-size:11px;cursor:pointer; }
    .stop-button:hover:not(:disabled) { background:#512b30; }
    .stop-button:disabled { opacity:.45;cursor:not-allowed; }
    .message-stop { margin-top:9px; }
    .stream { min-height:0;overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;scrollbar-gutter:stable;width:100%;margin:0;padding:18px clamp(18px,4vw,48px) 22px; }
    .day,#messages,#error { width:min(960px,100%);margin-left:auto;margin-right:auto; }
    .day { text-align:center;color:#7f8998;font-size:10px;letter-spacing:.06em;margin-top:5px;margin-bottom:22px; }
    #messages { display:grid;gap:18px; }
    .message { display:flex;gap:11px;max-width:88%;align-items:flex-start; }
    .message.user { margin-left:auto;flex-direction:row-reverse; }
    .message.user .avatar { background:#f2c879; }
    .message .avatar { width:30px;height:30px;border-radius:9px;font-size:12px; }
    .bubble { min-width:150px;max-width:100%;border:1px solid var(--line);border-radius:4px 13px 13px 13px;background:var(--panel);padding:11px 14px;box-shadow:0 6px 24px #0002; }
    .message.user .bubble { border-radius:13px 4px 13px 13px;background:#1b2430;border-color:#303e50; }
    .meta { color:#aeb8c8;font-size:11px;font-weight:650;display:flex;justify-content:space-between;gap:16px;margin-bottom:8px; }
    .time { color:#768092;font-weight:400;white-space:nowrap; }
    .content { color:#e1e5ec;white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px; }
    .empty { min-height:min(45vh,420px);display:grid;place-content:center;text-align:center;color:var(--muted); }
    .empty strong { color:#d8deea;font-size:15px;margin-bottom:7px; }
    .empty span { font-size:12px; }
    .pending { color:var(--muted);font-style:italic; }
    .failed { color:#ffaaa6; }
    .cancelled { color:var(--muted);font-style:italic; }
    .composer { position:relative;z-index:3;width:100%;margin:0;padding:10px clamp(18px,4vw,48px) 13px;border-top:1px solid var(--line);background:rgba(16,18,22,.94);backdrop-filter:blur(14px); }
    .compose-box { position:relative;border:1px solid #39414e;background:var(--panel);border-radius:14px;padding:11px 12px 9px;box-shadow:0 12px 36px #0003; }
    textarea { display:block;width:100%;min-height:60px;max-height:180px;resize:vertical;border:0;outline:0;background:transparent;color:var(--text);font:13px/1.5 "Segoe UI",system-ui,sans-serif; }
    .mention-suggestions { position:absolute;z-index:5;left:12px;right:12px;bottom:58px;display:grid;gap:3px;max-height:190px;overflow-y:auto;padding:5px;border:1px solid #39414e;border-radius:10px;background:#202630;box-shadow:0 12px 30px #0008; }
    .mention-suggestions[hidden] { display:none; }
    .mention-option { display:flex;align-items:center;justify-content:space-between;gap:12px;width:100%;padding:8px 10px;border:0;border-radius:7px;background:transparent;color:var(--text);text-align:left;cursor:pointer; }
    .mention-option:hover,.mention-option.active { background:#303947; }
    .mention-option-label { display:grid;gap:1px; }
    .mention-option-name { font-weight:650; }
    .mention-option-description { color:var(--muted);font-size:11px; }
    .mention-option-token { flex:none;color:#c9d9f5;font:12px/1.4 Consolas,"Courier New",monospace; }
    textarea::placeholder { color:#737d8c; }
    .compose-bottom { display:flex;align-items:center;justify-content:space-between;gap:12px;padding-top:8px;border-top:1px solid #292e37; }
    .compose-controls { display:flex;align-items:center;gap:10px;min-width:0; }
    .compose-actions { display:flex;align-items:center;gap:8px;flex:none; }
    .compose-hint { color:#7f8998;font-size:10px;line-height:1.4; }
    .send-button { border:0;border-radius:9px;padding:8px 14px;background:#b8d0f7;color:#111720;font-weight:700;cursor:pointer; }
    .send-button:hover:not(:disabled) { background:#c8dcfb; }
    .send-button:disabled, textarea:disabled { opacity:.55;cursor:not-allowed; }
    #error { color:#ffaaa6;padding:12px;display:none;text-align:center; }
    .agent-summary { border:1px solid var(--line);border-radius:10px;padding:10px;margin-bottom:16px;font-size:12px; }
    #task-panel { min-width:0; }
    #task-panel > summary { cursor:pointer;font-weight:650;padding:8px 0; }
    #collaboration-tasks { min-width:0; }
    #collaboration-tasks .bubble { min-width:0;margin:10px 0;overflow-wrap:anywhere; }
    #collaboration-tasks pre { white-space:pre-wrap;overflow-wrap:anywhere; }
    #collaboration-tasks .task-controls { display:flex;flex-wrap:wrap;gap:6px; }
    #collaboration-tasks input { min-width:0;max-width:100%; }
    #collaboration-tasks textarea { border:1px solid var(--line);padding:6px; }
    @media (max-width:760px) {
      .app { grid-template-columns:1fr;grid-template-rows:auto minmax(0,1fr); }
      aside { height:auto;max-height:45dvh;border-right:0;border-bottom:1px solid var(--line);padding:10px 14px;overflow-x:hidden; }
      .brand { padding:0 6px 9px; }
      .brand-sub { display:none; }
      .agent-summary { padding:6px 10px;margin-bottom:4px; }
      #collaboration-tasks { max-height:25dvh;overflow-y:auto; }
      main { height:auto; }
      .topbar { min-height:58px;padding:9px 14px; }
      .topbar .title { font-size:14px; }
      .topbar .subtitle { font-size:10px; }
      .tools { gap:8px; }
      select { max-width:42vw;padding:7px 23px 7px 8px;font-size:11px; }
      .refresh { gap:6px;font-size:10px; }
      .refresh-frequency { display:none; }
      .stream { padding:14px 12px 18px; }
      .message { max-width:96%; }
      .composer { padding:8px 10px 10px; }
      .compose-box { padding:9px 9px 8px; }
      textarea { min-height:52px; }
      .compose-bottom { align-items:flex-end;gap:8px; }
      .compose-hint { max-width:42vw;font-size:9px; }
      .compose-actions { gap:6px; }
      .send-button { padding:8px 10px;font-size:11px; }
      .stop-button { padding:6px 8px;font-size:10px; }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside id="task-sidebar">
      <div class="brand"><div class="logo">A</div><div><div class="brand-name">Agent 工作台</div><div class="brand-sub">本机对话 · Codex 与 DeepSeek Harness</div></div></div>
      <section class="agent-summary" aria-label="可用 Agent"><div id="agents"></div></section>
      <details id="task-panel" open>
        <summary id="task-count">任务（0）</summary>
        <div id="collaboration-tasks"></div>
      </details>
    </aside>
    <main>
      <header class="topbar">
        <div><div class="title">共享对话</div><div class="subtitle">通过 @ 提及选择接收对象</div></div>
        <div class="tools"><select id="conversation" aria-label="选择对话"></select><div class="refresh"><span class="dot"></span><div class="refresh-copy"><span id="status">正在连接</span><span class="refresh-frequency">每 3 秒自动更新</span></div></div></div>
      </header>
      <section class="stream" id="stream" aria-live="polite">
        <div class="day">消息按发送时间排列</div>
        <div id="messages"></div>
        <div id="error"></div>
      </section>
      <form class="composer" id="composer">
        <div class="compose-box">
          <textarea id="message-input" maxlength="20000" placeholder="输入消息……默认发给所有 Agent；输入 @ 查看候选，按 Tab 补全（Enter 发送，Shift+Enter 换行）" aria-label="输入消息" aria-autocomplete="list" aria-controls="mention-suggestions" aria-expanded="false"></textarea>
          <div class="mention-suggestions" id="mention-suggestions" role="listbox" aria-label="Agent 提及候选" hidden></div>
          <div class="compose-bottom"><div class="compose-controls"><span class="compose-hint" id="compose-hint">默认同时发送给所有 Agent；用 @ 提及可指定对象，普通 @ 字符不会触发路由。右侧 Stop 可停止全部运行中的 Agent。</span></div><div class="compose-actions"><button class="stop-button" id="stop-all" type="button" disabled>Stop</button><button class="send-button" id="send-button" type="submit">发送给所有 Agent</button></div></div>
        </div>
      </form>
    </main>
  </div>
<script>
const capabilityToken = "__CAPABILITY_TOKEN__";
const agentsNode = document.getElementById("agents");
const messagesNode = document.getElementById("messages");
const conversationNode = document.getElementById("conversation");
const errorNode = document.getElementById("error");
const statusNode = document.getElementById("status");
const streamNode = document.getElementById("stream");
const composerNode = document.getElementById("composer");
const inputNode = document.getElementById("message-input");
const sendButton = document.getElementById("send-button");
const stopAllButton = document.getElementById("stop-all");
const composeHint = document.getElementById("compose-hint");
const mentionSuggestionsNode = document.getElementById("mention-suggestions");
const mentionOptions = [
  {token:"@Codex", label:"Codex", description:"只由 Codex 回复", prefixes:["codex", "openai"]},
  {token:"@DeepSeek Harness", label:"DeepSeek Harness", description:"只由 DeepSeek Harness 回复", prefixes:["deepseek harness", "deepseekharness", "deepseek"]},
  {token:"@Grok", label:"Grok", description:"只由 Grok 回复", prefixes:["grok", "grok-build", "xai"]},
  {token:"@all", label:"所有 Agent", description:"同时发送给所有 Agent", prefixes:["all", "everyone", "所有", "全部"]}
];
let selectedConversation = "default";
let refreshVersion = 0;
let refreshPending = false;
let refreshPromise = null;
let initialized = false;
let previousMessageIds = "";
let pendingAgents = new Set();
let availableAgents = {codex:false, "deepseek-harness":false, grok:false};
let submitting = false;
let stopAllSubmitting = false;
let activeMentionIndex = 0;
let currentMentionKey = "";
let dismissedMentionStart = null;
let visibleMentionOptions = [];

function textElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function formatTime(value) {
  if (!value) return "未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit"});
}

async function getJson(path) {
  const controller = new AbortController();
  const timeout = setTimeout(function() { controller.abort(); }, 10000);
  try {
    const response = await fetch(path, {cache:"no-store", signal:controller.signal});
    if (!response.ok) throw new Error("HTTP " + response.status);
    return await response.json();
  } finally { clearTimeout(timeout); }
}

function agentAvailability(agents, codexAvailable, dshAvailable, grokAvailable) {
  return {
    codex: Boolean(codexAvailable),
    "deepseek-harness": Boolean(dshAvailable && agents.some(agent => agent.agent_id === "deepseek-harness")),
    grok: Boolean(grokAvailable)
  };
}

function renderAgents(availability) {
  const names = Object.keys(availability).filter(id => availability[id]).map(agentLabel);
  const label = names.join(" / ") || "暂无可用 Agent";
  if (agentsNode.textContent !== label) agentsNode.textContent = label;
}

function renderConversations(conversations) {
  if (!conversations.some(function(item) { return item.conversation_id === "default"; })) {
    conversations.unshift({conversation_id:"default", message_count:0});
  }
  const ids = conversations.map(function(item) { return item.conversation_id; });
  if (!ids.includes(selectedConversation)) selectedConversation = ids[0] || "default";
  conversationNode.replaceChildren();
  for (const item of conversations) {
    const option = document.createElement("option");
    option.value = item.conversation_id;
    option.textContent = item.conversation_id + " · " + item.message_count + " 条";
    conversationNode.append(option);
  }
  conversationNode.value = selectedConversation;
}

function renderRecipients(agents, codexAvailable, dshAvailable, grokAvailable) {
  availableAgents = agentAvailability(agents, codexAvailable, dshAvailable, grokAvailable);
  renderAgents(availableAgents);
}

function recipientsForText(content) {
  const masked = content.replace(/```[\s\S]*?(?:```|$)|`[^`\r\n]*`|https?:\/\/\S+/g, m => " ".repeat(m.length));
  // Grok joins broadcasts only while its CLI is available, so a missing Grok never blocks Codex/DSH.
  const everyone = availableAgents.grok ? ["codex", "deepseek-harness", "grok"] : ["codex", "deepseek-harness"];
  if (/(^|[^A-Za-z0-9_@\\])@(?:all|everyone|\u6240\u6709|\u5168\u90e8)(?![A-Za-z0-9_])/i.test(masked)) return everyone;
  const pattern = /(^|[^A-Za-z0-9_@\\])@(?:(codex|openai)|(deepseek\s+harness|deepseekharness|deepseek)|(grok-build|grok|xai))(?![A-Za-z0-9_]|\.[A-Za-z])/ig;
  const match = pattern.exec(masked);
  return match ? [match[2] ? "codex" : match[3] ? "deepseek-harness" : "grok"] : everyone;
}

function isInsideCode(text, position) {
  const before = text.slice(0, position);
  const fenceCount = (before.match(/```/g) || []).length;
  if (fenceCount % 2 === 1) return true;
  const lastFence = before.lastIndexOf("```");
  const inlineText = lastFence < 0 ? before : before.slice(lastFence + 3);
  return ((inlineText.match(/`/g) || []).length % 2) === 1;
}

function getMentionContext() {
  const caret = inputNode.selectionStart;
  if (caret !== inputNode.selectionEnd) return null;
  const beforeCaret = inputNode.value.slice(0, caret);
  const start = beforeCaret.lastIndexOf("@");
  if (start < 0 || isInsideCode(inputNode.value, start)) return null;
  const nextChar = inputNode.value[caret] || "";
  if (/[A-Za-z0-9_]/.test(nextChar)) return null;
  const previousChar = start > 0 ? inputNode.value[start - 1] : "";
  if (previousChar && /[A-Za-z0-9_@\\]/.test(previousChar)) return null;
  const query = beforeCaret.slice(start + 1);
  if (/[\r\n]/.test(query)) return null;
  const normalizedQuery = query.toLowerCase().replace(/\s+/g, " ");
  const matches = mentionOptions.filter(function(option) {
    return option.prefixes.some(function(prefix) {
      return !normalizedQuery || prefix.startsWith(normalizedQuery);
    });
  });
  if (!matches.length) return null;
  return {start:start, end:caret, key:start + ":" + query, options:matches};
}

function hideMentionSuggestions() {
  mentionSuggestionsNode.hidden = true;
  inputNode.setAttribute("aria-expanded", "false");
  mentionSuggestionsNode.replaceChildren();
  visibleMentionOptions = [];
  currentMentionKey = "";
  activeMentionIndex = 0;
}

function updateMentionSuggestions() {
  const context = getMentionContext();
  if (!context || context.start === dismissedMentionStart) {
    hideMentionSuggestions();
    return;
  }
  if (context.key !== currentMentionKey) {
    currentMentionKey = context.key;
    activeMentionIndex = 0;
  }
  visibleMentionOptions = context.options;
  mentionSuggestionsNode.replaceChildren();
  visibleMentionOptions.forEach(function(option, index) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "mention-option" + (index === activeMentionIndex ? " active" : "");
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", index === activeMentionIndex ? "true" : "false");
    button.dataset.index = String(index);
    const label = textElement("span", "mention-option-label");
    label.append(textElement("span", "mention-option-name", option.label));
    label.append(textElement("span", "mention-option-description", option.description));
    button.append(label);
    button.append(textElement("span", "mention-option-token", option.token));
    mentionSuggestionsNode.append(button);
  });
  mentionSuggestionsNode.hidden = false;
  inputNode.setAttribute("aria-expanded", "true");
}

function completeMention(index) {
  const context = getMentionContext();
  const option = visibleMentionOptions[index];
  if (!context || !option) return false;
  const suffix = inputNode.value.slice(context.end);
  const needsSpace = !suffix || !/^[\s,，。.!?！？:：;；]/.test(suffix);
  const insertion = option.token + (needsSpace ? " " : "");
  inputNode.value = inputNode.value.slice(0, context.start) + insertion + suffix;
  const caret = context.start + insertion.length;
  inputNode.setSelectionRange(caret, caret);
  dismissedMentionStart = context.start;
  hideMentionSuggestions();
  inputNode.focus();
  updateComposer();
  return true;
}

function agentLabel(agentId) {
  return ({codex:"Codex", "deepseek-harness":"DeepSeek Harness", grok:"Grok"})[agentId] || String(agentId);
}

function updateComposer() {
  updateMentionSuggestions();
  const recipients = recipientsForText(inputNode.value);
  const busy = recipients.filter(function(agentId) { return pendingAgents.has(agentId); });
  const unavailable = recipients.filter(function(agentId) { return !availableAgents[agentId]; });
  const recipientLabel = recipients.length >= 2
    ? "所有 Agent"
    : recipients.map(agentLabel).join(" 和 ");
  inputNode.disabled = submitting;
  sendButton.disabled = submitting || busy.length > 0 || unavailable.length > 0;
  sendButton.textContent = submitting ? "正在发送…" : "发送给" + recipientLabel;
  if (submitting) {
    composeHint.textContent = "正在提交消息…";
  } else if (!mentionSuggestionsNode.hidden) {
    composeHint.textContent = "用 ↑↓ 选择候选，按 Tab 补全；按 Enter 发送当前文本，按 Esc 关闭候选。";
  } else if (unavailable.length) {
    composeHint.textContent = unavailable.map(agentLabel).join("、")
      + " 当前不可用；请检查该 Agent 的 CLI 和登记状态，或只 @ 可用的 Agent。";
  } else if (busy.length) {
    composeHint.textContent = busy.map(agentLabel).join("、")
      + " 正在处理当前对话。可以 @ 另一个空闲 Agent 并行发送。";
  } else {
    composeHint.textContent = "默认同时发送给所有 Agent；输入 @ 查看候选，按 Tab 补全；不补全则按当前文本发送。邮箱、代码和转义的 @ 不触发；"
      + String.fromCharCode(92) + "@Codex 可按原文发送。";
  }
}

function renderMessages(messages) {
  const nearBottom = streamNode.scrollHeight - streamNode.scrollTop - streamNode.clientHeight < 100;
  const ids = messages.map(function(item) { return item.source + ":" + item.id + ":" + item.status; }).join(",");
  const changed = ids !== previousMessageIds;
  previousMessageIds = ids;
  messagesNode.replaceChildren();
  pendingAgents = new Set(messages
    .filter(function(item) { return item.status === "pending"; })
    .map(function(item) { return item.sender_id; }));
  updateComposer();
  if (!messages.length) {
    const empty = textElement("div", "empty");
    empty.append(textElement("strong", "", "开始一段对话"));
    empty.append(textElement("span", "", "发送一条消息即可同时获得所有 Agent 的回复，也可以用 @ 指定对象。"));
    messagesNode.append(empty);
    return;
  }
  for (const message of messages) {
    const isUser = message.sender_id === "user";
    const row = textElement("article", "message" + (isUser ? " user" : ""));
    const avatar = textElement("div", "avatar" + (String(message.sender_id).includes("deepseek") ? " deepseek" : message.sender_id === "grok" ? " grok" : ""), String(message.sender_name || message.sender_id || "?").slice(0,1));
    const bubble = textElement("div", "bubble");
    const meta = textElement("div", "meta");
    meta.append(textElement("span", "", (message.sender_name || message.sender_id) + " → " + (message.recipient_name || message.recipient_id)));
    meta.append(textElement("time", "time", formatTime(message.created_at)));
    bubble.append(meta);
    let content = message.content || "";
    let contentClass = "content";
    if (message.status === "pending") { content = (message.sender_name || "Agent") + " 正在思考…"; contentClass += " pending"; }
    if (message.status === "failed") contentClass += " failed";
    if (message.status === "cancelled") contentClass += " cancelled";
    const protocolStart = content.indexOf("```agent-collaboration");
    if (protocolStart >= 0 && message.status === "completed" && !isUser) {
      bubble.append(textElement("div", contentClass, content.slice(0,protocolStart).trim()));
      const protocolDetails = document.createElement("details");
      protocolDetails.append(textElement("summary", "", "协作协议（调试）"), textElement("pre", "content", content.slice(protocolStart)));
      bubble.append(protocolDetails);
    } else { bubble.append(textElement("div", contentClass, content)); }
    if (message.status === "pending" && message.source === "workbench") {
      const stopButton = textElement("button", "stop-button message-stop", "停止此 Agent");
      stopButton.type = "button";
      stopButton.dataset.stopMessageId = String(message.id);
      stopButton.setAttribute("aria-label", "停止" + (message.sender_name || message.sender_id));
      bubble.append(stopButton);
    }
    row.append(avatar, bubble);
    messagesNode.append(row);
  }
  if (!initialized || (changed && nearBottom)) streamNode.scrollTop = streamNode.scrollHeight;
  initialized = true;
}

// Task controls use textContent throughout, including Agent/user supplied text.
const collaborationDrafts = new Map();
const collaborationRoot = document.createElement("section");
collaborationRoot.setAttribute("aria-label", "Agent 协作任务");
const taskPanel = document.getElementById("task-panel");
const taskCount = document.getElementById("task-count");
const taskHost = document.getElementById("collaboration-tasks");
const taskSidebar = document.getElementById("task-sidebar");
taskHost.append(collaborationRoot);
if (window.matchMedia("(max-width:760px)").matches) taskPanel.open = false;
const collaborationCards = new Map();
let collaborationSignature = "";
let collaborationConversation = selectedConversation;
let collaborationEpoch = 0;

function resetCollaboration(conversation) {
  collaborationConversation = conversation;
  collaborationEpoch++;
  collaborationSignature = "";
  collaborationCards.clear();
  collaborationRoot.replaceChildren();
  taskCount.textContent = "任务（0）";
}

async function collaborationAction(task, action, button, content = "", roundLimit = null) {
  if (button.dataset.conversation !== selectedConversation || button.dataset.epoch !== String(collaborationEpoch)) return;
  button.disabled = true;
  const conversation = selectedConversation;
  const operationId = button.dataset.operationId || crypto.randomUUID();
  button.dataset.operationId = operationId;
  try {
    const body = {job_id:task.state.job_id, conversation_id:conversation, operation_id:operationId, action, content};
    if (roundLimit !== null) body.round_limit = roundLimit;
    const response = await fetch("/api/collaboration/action", {
      method:"POST", headers:{"Content-Type":"application/json", "X-Capability-Token":capabilityToken},
      body:JSON.stringify(body)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "操作失败");
    delete button.dataset.operationId;
    collaborationSignature = "";
    if (action === "answer") collaborationDrafts.delete(task.state.job_id);
    await refreshAll();
  } catch (error) {
    alert(String(error.message || error));
  } finally { button.disabled = false; }
}

async function gitFeatureAction(task, action, button, remote = null) {
  if (button.dataset.conversation !== selectedConversation) return;
  button.disabled = true;
  try {
    const response = await fetch('/api/git/action', {
      method:'POST', headers:{'Content-Type':'application/json','X-Capability-Token':capabilityToken},
      body:JSON.stringify({job_id:task.state.job_id,conversation_id:selectedConversation,action,remote})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Git操作失败');
    collaborationSignature = '';
  } catch(error) { alert(String(error.message || error)); }
  finally { button.disabled = false; }
}

function renderCollaboration(tasks, conversation) {
  if (conversation !== selectedConversation) return;
  if (conversation !== collaborationConversation) resetCollaboration(conversation);
  const signature = JSON.stringify([conversation,tasks]);
  if (signature === collaborationSignature) return;
  collaborationSignature = signature;
  const sidebarScroll = taskSidebar.scrollTop;
  const hostScroll = taskHost.scrollTop;
  taskCount.textContent = "任务（" + tasks.length + "）";
  const ids = new Set(tasks.map(task => task.state.job_id));
  for (const [id, saved] of collaborationCards) {
    if (!ids.has(id)) { saved.card.remove(); collaborationCards.delete(id); }
  }
  const labels = {working:"协作中",waiting_user:"等待你回答",blocked:"受阻",quiescing:"正在停止后续工作",
    final_ready:"等待最终汇总",final_delivery:"最终汇总中",completed:"已交付",failed:"失败",interrupted:"已中断",stopped:"已停止"};
  for (const task of tasks) {
    const state = task.state;
    const saved = collaborationCards.get(state.job_id);
    const taskSignature = JSON.stringify(task);
    if (saved && saved.signature === taskSignature) continue;
    const card = saved ? saved.card : textElement("article", "bubble");
    const oldControls = card.querySelector(".task-controls");
    if (oldControls && Array.from(oldControls.children).some(node => node.disabled)) {
      collaborationSignature = "";
      continue;
    }
    const oldDetails = card.querySelector("details");
    const oldAnswer = card.querySelector("textarea");
    const oldLimit = card.querySelector(".round-limit-input");
    const focused = document.activeElement;
    const focusKind = oldAnswer && focused === oldAnswer ? "textarea" : oldLimit && focused === oldLimit ? "input" : null;
    const selection = oldAnswer && [oldAnswer.selectionStart, oldAnswer.selectionEnd];
    if (oldAnswer) collaborationDrafts.set(state.job_id, oldAnswer.value);
    card.replaceChildren();
    card.append(textElement("strong", "", (labels[state.phase] || state.phase) + " · " + state.used + "/" + state.round_limit + " 轮"));
    card.append(textElement("div", "", "负责人：" + agentLabel(task.plan.owner_id) + "；最终答复：" + agentLabel(task.plan.final_responder_id)));
    if (state.reason) card.append(textElement("p", "", state.reason));
    const details = document.createElement("details");
    details.open = oldDetails ? oldDetails.open : false;
    details.append(textElement("summary", "", "需求与分工"), textElement("pre", "content", task.request + "\n" + task.plan.tasks.map(t => t.agent_id + ": " + t.instruction).join("\n")));
    card.append(details);
    const controls = document.createElement("div");
    controls.className = "task-controls";
    if (task.git_feature) {
      const git = task.git_feature;
      card.append(textElement('p','', 'Git：' + git.state + ' · ' + git.branch +
        (git.result && git.result.commit_id ? '\n提交：' + git.result.commit_id : '')));
      if (['working','interrupted'].includes(state.phase)) {
        function gitButton(label, action, remote = null) {
          const button = textElement('button','stop-button',label);
          button.dataset.conversation = conversation;
          button.onclick = () => gitFeatureAction(task,action,button,remote);
          controls.append(button);
        }
        if (git.needs_login) gitButton('登录GitHub','login');
        if (!git.remote) for (const remote of git.remotes || []) gitButton('使用仓库远端 ' + remote,'remote',remote);
        if (!git.remote && !(git.remotes || []).length) {
          const repository = document.createElement('input');
          repository.placeholder = 'GitHub仓库地址：https://github.com/用户/仓库';
          repository.setAttribute('aria-label','GitHub仓库地址');
          controls.append(repository);
          const choose = textElement('button','stop-button','保存仓库');
          choose.dataset.conversation = conversation;
          choose.onclick = () => gitFeatureAction(task,'remote',choose,repository.value);
          controls.append(choose);
        }
        gitButton('重试原Git操作','retry');
      }
    }
    function add(label, action, contentProvider) {
      const button = textElement("button", "stop-button", label);
      button.type = "button";
      button.dataset.conversation = conversation;
      button.dataset.epoch = String(collaborationEpoch);
      button.onclick = () => collaborationAction(task,action,button,contentProvider ? contentProvider() : "");
      controls.append(button);
    }
    if (["waiting_user","blocked"].includes(state.phase)) {
      let questions = [];
      try { questions = JSON.parse(state.questions); } catch (_) {}
      card.append(textElement("p", "", "需要你回答：" + (questions.join("；") || state.reason)));
      const answer = document.createElement("textarea");
      answer.placeholder = "补充信息后继续，也可直接收尾";
      answer.value = collaborationDrafts.get(state.job_id) || "";
      answer.oninput = () => collaborationDrafts.set(state.job_id,answer.value);
      card.append(answer);
      add("提交回答并继续","answer",() => answer.value);
    }
    if (["working","waiting_user","blocked","final_ready"].includes(state.phase)) add("按当前成果给结论","finalize");
    if (!["completed","stopped","interrupted"].includes(state.phase)) add("立即停止","stop");
    if (["working","waiting_user","blocked"].includes(state.phase)) {
      const limit = document.createElement("input");
      limit.className = "round-limit-input";
      limit.type = "number"; limit.min = "2"; limit.max = "100"; limit.value = state.round_limit;
      if (oldLimit && String(oldLimit.value) !== String(saved.roundLimit)) limit.value = oldLimit.value;
      limit.setAttribute("aria-label", "轮次上限");
      controls.append(limit);
      const save = textElement("button", "stop-button", "调整上限");
      save.dataset.conversation = conversation;
      save.dataset.epoch = String(collaborationEpoch);
      save.onclick = () => collaborationAction(task,"set_limit",save,"",Number(limit.value));
      controls.append(save);
    }
    card.append(controls);
    if (["stopped","completed","interrupted"].includes(state.phase)) {
      card.append(textElement("p", "", "已有改动未自动回退；本任务可能涉及文件修改，范围及完整性未经核实。"));
    }
    collaborationCards.set(state.job_id, {card, signature:taskSignature, roundLimit:state.round_limit});
    if (!saved) collaborationRoot.append(card);
    if (focusKind) {
      const replacement = card.querySelector(focusKind);
      if (replacement) {
        replacement.focus({preventScroll:true});
        if (focusKind === "textarea") replacement.setSelectionRange(...selection);
      }
    }
  }
  tasks.forEach((task, index) => {
    const card = collaborationCards.get(task.state.job_id).card;
    if (collaborationRoot.children[index] !== card)
      collaborationRoot.insertBefore(card, collaborationRoot.children[index] || null);
  });
  taskSidebar.scrollTop = sidebarScroll;
  taskHost.scrollTop = hostScroll;
}

async function refreshRound(version, conversation) {
  try {
    const results = await Promise.all([
      getJson("/api/agents"),
      getJson("/api/conversations"),
      getJson("/api/runs"),
      getJson("/api/collaboration/tasks?conversation_id=" + encodeURIComponent(conversation))
    ]);
    if (version !== refreshVersion || conversation !== selectedConversation) return;
    renderCollaboration(results[3].tasks || [], conversation);
    renderRecipients(results[0].agents, results[0].codex_cli_available, results[0].dsh_cli_available, results[0].grok_cli_available);
    renderConversations(results[1].conversations);
    if (conversation !== selectedConversation) {
      refreshVersion++;
      refreshPending = true;
      resetCollaboration(selectedConversation);
      return;
    }
    const activeRuns = results[2].runs || [];
    stopAllButton.disabled = stopAllSubmitting || activeRuns.length === 0;
    stopAllButton.textContent = activeRuns.length ? "Stop (" + activeRuns.length + ")" : "Stop";
    const data = await getJson("/api/messages?conversation_id=" + encodeURIComponent(conversation) + "&limit=500");
    if (version !== refreshVersion || conversation !== selectedConversation) return;
    renderMessages(data.messages);
    if (version !== refreshVersion || conversation !== selectedConversation) return;
    errorNode.style.display = "none";
    statusNode.textContent = "已更新 · " + new Date().toLocaleTimeString("zh-CN");
  } catch (error) {
    if (version !== refreshVersion || conversation !== selectedConversation) return;
    errorNode.textContent = "无法读取桥接数据库：" + error.message;
    errorNode.style.display = "block";
    statusNode.textContent = "连接失败";
  }
}

function refreshAll(periodic = false) {
  if (periodic && refreshPromise) return refreshPromise;
  if (!periodic) refreshVersion++;
  refreshPending = true;
  if (!refreshPromise) {
    refreshPromise = Promise.resolve().then(async function() {
      try {
        while (refreshPending) {
          refreshPending = false;
          await refreshRound(refreshVersion, selectedConversation);
        }
      } finally { refreshPromise = null; }
    });
  }
  return refreshPromise;
}

async function requestStop(payload, button) {
  if (button.disabled) return;
  button.disabled = true;
  if (button === stopAllButton) stopAllSubmitting = true;
  let failed = false;
  try {
    const response = await fetch("/api/stop", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify(payload)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "HTTP " + response.status);
    statusNode.textContent = "已请求停止 " + result.stopped + " 个 Agent";
  } catch (error) {
    failed = true;
    errorNode.textContent = "停止失败：" + error.message;
    errorNode.style.display = "block";
  } finally {
    if (button === stopAllButton) stopAllSubmitting = false;
    if (!failed) await refreshAll();
  }
}

stopAllButton.addEventListener("click", function() {
  requestStop({all:true}, stopAllButton);
});
messagesNode.addEventListener("click", function(event) {
  const button = event.target.closest("button[data-stop-message-id]");
  if (!button) return;
  requestStop({message_id:Number(button.dataset.stopMessageId)}, button);
});

conversationNode.addEventListener("change", function() {
  selectedConversation = conversationNode.value;
  previousMessageIds = "";
  initialized = false;
  resetCollaboration(selectedConversation);
  refreshAll();
});
composerNode.addEventListener("submit", async function(event) {
  event.preventDefault();
  const content = inputNode.value.trim();
  if (!content || submitting) return;
  const recipients = recipientsForText(content);
  if (recipients.some(function(agentId) {
    return pendingAgents.has(agentId) || !availableAgents[agentId];
  })) return;
  const mentionContext = getMentionContext();
  if (mentionContext) dismissedMentionStart = mentionContext.start;
  hideMentionSuggestions();
  submitting = true;
  updateComposer();
  try {
    const response = await fetch("/api/messages", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({conversation_id:selectedConversation, content:content, recipients:recipients})
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "HTTP " + response.status);
    inputNode.value = "";
    previousMessageIds = "";
    initialized = false;
    await refreshAll();
  } catch (error) {
    errorNode.textContent = "发送失败：" + error.message;
    errorNode.style.display = "block";
  } finally {
    submitting = false;
    updateComposer();
  }
});
inputNode.addEventListener("input", function(event) {
  if (event.inputType && event.inputType.startsWith("delete")) {
    const context = getMentionContext();
    if (context) dismissedMentionStart = context.start;
  } else {
    dismissedMentionStart = null;
  }
  updateComposer();
});
inputNode.addEventListener("compositionend", function() {
  dismissedMentionStart = null;
  updateComposer();
});
inputNode.addEventListener("click", updateComposer);
inputNode.addEventListener("keyup", function(event) {
  if (["Enter", "Tab", "Escape"].includes(event.key)) return;
  if (!mentionSuggestionsNode.hidden && ["ArrowUp", "ArrowDown"].includes(event.key)) return;
  updateComposer();
});
mentionSuggestionsNode.addEventListener("mousedown", function(event) {
  event.preventDefault();
});
mentionSuggestionsNode.addEventListener("click", function(event) {
  const button = event.target.closest("button[data-index]");
  if (button) completeMention(Number(button.dataset.index));
});
function dismissMentionSuggestions() {
  const context = getMentionContext();
  if (context) dismissedMentionStart = context.start;
  hideMentionSuggestions();
  updateComposer();
}
inputNode.addEventListener("blur", dismissMentionSuggestions);
document.addEventListener("pointerdown", function(event) {
  if (mentionSuggestionsNode.hidden) return;
  if (!inputNode.contains(event.target) && !mentionSuggestionsNode.contains(event.target)) {
    dismissMentionSuggestions();
  }
});
inputNode.addEventListener("keydown", function(event) {
  if (event.isComposing || event.keyCode === 229) return;
  if (!mentionSuggestionsNode.hidden && visibleMentionOptions.length) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      activeMentionIndex = (activeMentionIndex + 1) % visibleMentionOptions.length;
      updateMentionSuggestions();
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      activeMentionIndex = (activeMentionIndex - 1 + visibleMentionOptions.length) % visibleMentionOptions.length;
      updateMentionSuggestions();
      return;
    }
    if (event.key === "Tab") {
      event.preventDefault();
      completeMention(activeMentionIndex);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      dismissMentionSuggestions();
      return;
    }
  }
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    composerNode.requestSubmit();
  }
});
// WORKBENCH_STARTUP
refreshAll();
setInterval(function() { refreshAll(true); }, 3000);
</script>
</body>
</html>"""


def open_database() -> sqlite3.Connection:
    if not DB_PATH.is_file():
        raise FileNotFoundError(f"找不到桥接数据库：{DB_PATH}")
    connection = sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def get_working_directory_text(conversation_id: str) -> str:
    with closing(open_database()) as connection:
        row = connection.execute(
            "SELECT working_directory FROM workbench_conversation_settings WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
    return row["working_directory"] if row else str(PROJECT_DIR)


def resolve_working_directory(conversation_id: str) -> Path:
    configured = Path(get_working_directory_text(conversation_id)).expanduser()
    try:
        resolved = configured.resolve(strict=True)
    except OSError as error:
        raise FileNotFoundError(f"工作目录不存在或无法访问：{configured}") from error
    if not resolved.is_dir():
        raise NotADirectoryError(f"工作目录不是文件夹：{resolved}")
    return resolved


def normalize_working_directory(value: str) -> str:
    value = os.path.expandvars(value.strip().strip('"'))
    if not value:
        raise ValueError("请输入工作目录的绝对路径")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise ValueError("工作目录必须使用绝对路径")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError(f"工作目录不存在或无法访问：{candidate}") from error
    if not resolved.is_dir():
        raise ValueError(f"指定路径不是文件夹：{resolved}")
    return str(resolved)


def save_working_directory(conversation_id: str, working_directory: str) -> bool:
    # Settings holds the short registry gate: fail fast on DB contention.
    connection = connect_workbench(timeout=0)
    try:
        with connection:
            row = connection.execute(
                "SELECT working_directory FROM workbench_conversation_settings WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            old_value = row[0] if row else str(PROJECT_DIR)
            if old_value == working_directory:
                return False
            connection.execute(
                """INSERT INTO workbench_conversation_settings
                   (conversation_id, working_directory, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(conversation_id) DO UPDATE SET
                       working_directory = excluded.working_directory,
                       updated_at = excluded.updated_at""",
                (conversation_id, working_directory, utc_now()),
            )
            # CLI sessions are tied to the directory where they were created.
            # Keep the transcript; enabled task-5 bindings require explicit validation/adoption.
            if SESSION_CONFIGURATION_RESOLVER is not None:
                invalidate_sessions(connection, conversation_id)
            else:
                # Pre-cutover 4B compatibility only; never used by the enabled task-5 path.
                connection.execute("DELETE FROM workbench_sessions WHERE conversation_id = ?", (conversation_id,))
                connection.execute("DELETE FROM workbench_agent_sessions WHERE conversation_id = ?", (conversation_id,))
            return True
    finally:
        connection.close()


def fetch_agents() -> list[dict]:
    with closing(open_database()) as connection:
        rows = connection.execute(
            """SELECT agent_id, name, vendor, model, role, registered_at, last_seen
               FROM agents ORDER BY registered_at, agent_id"""
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_conversations() -> list[dict]:
    with closing(open_database()) as connection:
        bridge_rows = connection.execute(
            """SELECT conversation_id, COUNT(*) AS message_count, MAX(created_at) AS latest_at
               FROM messages GROUP BY conversation_id"""
        ).fetchall()
        workbench_rows = connection.execute(
            """SELECT conversation_id, COUNT(*) AS message_count, MAX(created_at) AS latest_at
               FROM workbench_messages GROUP BY conversation_id"""
        ).fetchall()
    conversations: dict[str, dict] = {}
    for row in [*bridge_rows, *workbench_rows]:
        item = conversations.setdefault(row["conversation_id"], {
            "conversation_id": row["conversation_id"],
            "message_count": 0,
            "latest_at": "",
        })
        item["message_count"] += row["message_count"]
        item["latest_at"] = max(item["latest_at"], row["latest_at"] or "")
    return sorted(conversations.values(), key=lambda item: item["latest_at"], reverse=True)


def fetch_messages(conversation_id: str, limit: int) -> list[dict]:
    with closing(open_database()) as connection:
        bridge_rows = connection.execute(
            """SELECT m.id, m.conversation_id, m.sender_id,
                      COALESCE(sender.name, m.sender_id) AS sender_name,
                      m.recipient_id,
                      COALESCE(recipient.name, m.recipient_id) AS recipient_name,
                      m.content, m.created_at, 'completed' AS status, 'bridge' AS source
               FROM messages AS m
               LEFT JOIN agents AS sender ON sender.agent_id = m.sender_id
               LEFT JOIN agents AS recipient ON recipient.agent_id = m.recipient_id
               WHERE m.conversation_id = ?
               ORDER BY m.id DESC LIMIT ?""",
            (conversation_id, limit),
        ).fetchall()
        workbench_rows = connection.execute(
            """SELECT m.id, m.conversation_id, m.sender_id,
                      CASE WHEN m.sender_id = 'user' THEN '你'
                           ELSE COALESCE(sender.name, m.sender_id) END AS sender_name,
                      m.recipient_id,
                      CASE WHEN m.recipient_id = 'user' THEN '你'
                           WHEN m.recipient_id = 'all' THEN '所有 Agent'
                           ELSE COALESCE(recipient.name, m.recipient_id) END AS recipient_name,
                      m.content, m.created_at, m.status, 'workbench' AS source
               FROM workbench_messages AS m
               LEFT JOIN agents AS sender ON sender.agent_id = m.sender_id
               LEFT JOIN agents AS recipient ON recipient.agent_id = m.recipient_id
               WHERE m.conversation_id = ?
               ORDER BY m.id DESC LIMIT ?""",
            (conversation_id, limit),
        ).fetchall()
    messages = [dict(row) for row in bridge_rows]
    messages.extend(dict(row) for row in workbench_rows)
    messages.sort(key=lambda item: (item["created_at"], item["source"], item["id"]))
    return messages[-limit:]


def get_codex_thread_id(conversation_id: str) -> str | None:
    with closing(open_database()) as connection:
        row = connection.execute(
            "SELECT codex_thread_id FROM workbench_sessions WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
    return row["codex_thread_id"] if row else None


def save_codex_thread_id(conversation_id: str, thread_id: str) -> None:
    if SESSION_CONFIGURATION_RESOLVER is not None:
        raise RuntimeError('Legacy session storage is read-only after task-5 cutover')
    connection = connect_workbench(timeout=10)
    try:
        with connection:
            connection.execute(
                """INSERT INTO workbench_sessions (conversation_id, codex_thread_id, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(conversation_id) DO UPDATE SET
                       codex_thread_id = excluded.codex_thread_id,
                       updated_at = excluded.updated_at""",
                (conversation_id, thread_id, utc_now()),
            )
    finally:
        connection.close()


def get_agent_session_id(conversation_id: str, agent_id: str) -> str | None:
    with closing(open_database()) as connection:
        row = connection.execute(
            """SELECT session_id FROM workbench_agent_sessions
               WHERE conversation_id = ? AND agent_id = ?""",
            (conversation_id, agent_id),
        ).fetchone()
    return row["session_id"] if row else None


def save_agent_session_id(conversation_id: str, agent_id: str, session_id: str) -> None:
    if SESSION_CONFIGURATION_RESOLVER is not None:
        raise RuntimeError('Legacy session storage is read-only after task-5 cutover')
    connection = connect_workbench(timeout=10)
    try:
        with connection:
            connection.execute(
                """INSERT INTO workbench_agent_sessions
                   (conversation_id, agent_id, session_id, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(conversation_id, agent_id) DO UPDATE SET
                       session_id = excluded.session_id,
                       updated_at = excluded.updated_at""",
                (conversation_id, agent_id, session_id, utc_now()),
            )
    finally:
        connection.close()


def resolve_dsh_command() -> str | None:
    configured = os.environ.get("DSH_CLI", "dsh").strip().strip('"')
    if not configured:
        return None
    located = shutil.which(configured)
    if located:
        return located
    candidate = Path(configured).expanduser()
    if candidate.is_file():
        return str(candidate)
    return None


def resolve_grok_command() -> str | None:
    configured = os.environ.get("GROK_CLI", "").strip().strip('"')
    if configured:
        located = shutil.which(configured)
        if located:
            return located
        candidate = Path(configured).expanduser()
        return str(candidate) if candidate.is_file() else None
    located = shutil.which("grok")
    if located:
        return located
    candidate = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".grok" / "bin" / ("grok.exe" if os.name == "nt" else "grok")
    return str(candidate) if candidate.is_file() else None


def resolve_codex_command() -> str | None:
    configured = os.environ.get("CODEX_CLI", "").strip().strip('"')
    if configured:
        located = shutil.which(configured)
        if located:
            return located
        candidate = Path(configured).expanduser()
        return str(candidate) if candidate.is_file() else None

    located = shutil.which("codex")
    if located:
        return located

    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    codex_bin = local_app_data / "OpenAI" / "Codex" / "bin"
    candidates = list(codex_bin.glob("*/codex.exe")) if codex_bin.is_dir() else []
    if not candidates:
        return None
    candidates.sort(key=lambda candidate: candidate.stat().st_mtime, reverse=True)
    return str(candidates[0])


def update_codex_message(message_id: int, content: str, status: str) -> None:
    update_workbench_message(message_id, content, status)


def update_workbench_message(message_id: int, content: str, status: str) -> None:
    validating = getattr(COLLAB_CONTEXT, "validating_entry", False)
    received = status == "completed"
    if validating:
        COLLAB_CONTEXT.entry_reply_received = False
        if received:
            # Use the existing durable state; transport success is tracked
            # separately so an untouched pending row cannot be accepted.
            status = "pending"
    connection = connect_workbench(timeout=10)
    try:
        with connection:
            cursor = connection.execute(
                "UPDATE workbench_messages SET content = ?, status = ?, created_at = ? WHERE id = ?"
                + " AND status='pending'",
                (content, status, utc_now(), message_id),
            )
        if validating:
            COLLAB_CONTEXT.entry_reply_received = received and cursor.rowcount == 1
    finally:
        connection.close()


@dataclass(frozen=True)
class WorkerResult:
    status: str
    text: str
    process_result: object = None
    session_validation: object = None


def stage_collaboration_reply(message_id, job_id, call_id, text):
    with closing(connect_workbench()) as db, db:
        changed = db.execute("""UPDATE workbench_messages SET content=? WHERE id=? AND status='pending'
            AND EXISTS (SELECT 1 FROM execution_runs r WHERE r.source_message_id=workbench_messages.id
                        AND r.source_job_id=? AND r.source_call_id=?)""",
            (text, message_id, job_id, call_id)).rowcount
        if not changed:
            row = db.execute('SELECT status FROM workbench_messages WHERE id=?', (message_id,)).fetchone()
            if not row or row[0] != 'cancelled':
                raise RuntimeError('Collaboration staging association/state mismatch')


def finalize_message(db, *, message_id, job_id, call_id, disposition, reason=''):
    """Participate in the caller's reception transaction; never commit here."""
    row = db.execute("""SELECT m.status,m.content FROM workbench_messages m
        JOIN execution_runs r ON r.source_message_id=m.id
        JOIN collab_calls c ON c.job_id=r.source_job_id AND c.call_id=r.source_call_id
        WHERE m.id=? AND r.source_job_id=? AND r.source_call_id=?
          AND m.sender_id=c.agent_id""", (message_id,job_id,call_id)).fetchone()
    if not row:
        raise RuntimeError('Message/call association mismatch')
    if disposition == 'inspect':
        return row[0]
    if disposition == 'duplicate' or row[0] != 'pending':
        return  # Preserve cancellation and every established terminal state.
    status = {'accepted':'completed', 'rejected':'failed',
              'cancelled':'cancelled', 'superseded':'cancelled'}[disposition]
    # Superseded is an obsolete reply, not a protocol failure; existing cancelled
    # presentation avoids adding a schema/protocol status or leaving pending.
    suffix = ('\n[协作回复未接收] ' + reason if disposition == 'rejected' else
              '\n[回复已失效] ' + reason if disposition == 'superseded' else '')
    db.execute("UPDATE workbench_messages SET content=?,status=? WHERE id=? AND status='pending'",
               (row[1] + suffix,status,message_id))


def worker_reply(message_id, text, status):
    run = active_run(message_id)
    execution = run.get('execution') if run else None
    result = WorkerResult(status, text, execution.result if execution else None,
                          execution.session_validation if execution else None)
    if status == 'completed' and run and run.get('collaboration'):
        stage_collaboration_reply(message_id, *run['collaboration'], text)
    else:
        update_workbench_message(message_id, text, status)
    return result


def structured_worker(worker):
    @wraps(worker)
    def execute(conversation, prompt, message_id, user_message_id):
        result = worker(conversation, prompt, message_id, user_message_id)
        if not isinstance(result, WorkerResult) or result.status not in {'completed','failed','cancelled'} or not isinstance(result.text, str):
            raise RuntimeError('Worker returned invalid structured transport result')
        if result.status == 'completed' and (result.process_result is None or result.session_validation is None):
            raise RuntimeError('Successful worker result lacks execution evidence')
        return result
    return execute


def active_run(message_id):
    # Never retain the registry lock when taking a per-run lock or entering DB.
    with ACTIVE_RUNS_LOCK:
        return ACTIVE_RUNS.get(message_id)


def begin_execution(message_id, *, source_kind, job_id=None, call_id=None):
    run = active_run(message_id)
    if not run:
        raise RuntimeError('Missing active execution')
    with run['state_lock']:
        if run['claimed']:
            raise RuntimeError('Duplicate execution entry')
        run['claimed'] = True
        if run['cancel_requested']:
            raise AgentRunCancelled()
        try:
            directory = resolve_working_directory(run['conversation_id'])
            run['execution'] = Execution(DB_PATH, instance_id=SERVICE_INSTANCE_ID,
                agent_id=run['agent_id'], conversation_id=run['conversation_id'],
                working_directory=directory, source_kind=source_kind, message_id=message_id,
                job_id=job_id, call_id=call_id)
        except DomainBusy:
            # DomainBusy is an established admission contract; keep it catchable.
            raise
        except Exception as error:
            # Assignment only happens after successful reservation; no tokenless finish.
            raise ExecutionAdmissionError(error) from error
        return run['execution']


def finish_execution(message_id, *, outcome=None):
    run = active_run(message_id)
    if not run or not run.get('execution'):
        return False
    execution = run['execution']
    try:
        row = None
        status_error = None
        try:
            if outcome is None:
                with closing(connect_workbench()) as db:
                    row = db.execute('SELECT status FROM workbench_messages WHERE id=?', (message_id,)).fetchone()
        except Exception as error:
            # A failed message read must not bypass not_started finalization.
            status_error = error
        outcome = ('failed' if run.get('failed') else outcome if outcome is not None else
                   row[0] if row and row[0] in {'completed', 'cancelled'} else 'failed')
        if not execution.finish(outcome):
            raise RuntimeError('退出未确认；保留执行域占用，已停止后续派发，需人工核验恢复。')
        if status_error is not None:
            raise ExecutionAdmissionError(status_error) from status_error
        if execution.dispatch_error:
            # A known unstarted/finished process may release its domain, while a
            # session conflict still stops this collaboration drive (no replay).
            return False
        return True
    except Exception as error:
        logging.exception('Execution release failed for message %s', message_id)
        # A release fault is visible even when a reply was already written.
        try:
            notice = ('\n[本次执行已收尾，但状态读取失败，停止派发] ' if execution.released else
                      '\n[执行域未释放，停止派发；需人工核验] ')
            with closing(connect_workbench()) as db, db:
                db.execute("UPDATE workbench_messages SET status=CASE WHEN status='cancelled' THEN status ELSE 'failed' END,content=content || ? WHERE id=?",
                           (notice + format_execution_error(error)[:1000], message_id))
        except Exception:
            logging.exception('Could not persist execution release fault')
        return False


def register_active_run(message_id: int, conversation_id: str, agent_id: str) -> None:
    with ACTIVE_RUNS_LOCK:
        if message_id in ACTIVE_RUNS:
            return
        ACTIVE_RUNS[message_id] = {
            "message_id": message_id,
            "conversation_id": conversation_id,
            "agent_id": agent_id,
            "process": None,
            "state_lock": threading.RLock(),
            "claimed": False,
            "execution": None,
            "cancel_requested": False,
            "started_at": utc_now(),
        }


def unregister_active_run(message_id: int) -> None:
    with ACTIVE_RUNS_LOCK:
        ACTIVE_RUNS.pop(message_id, None)


def is_run_cancelled(message_id: int) -> bool:
    with ACTIVE_RUNS_LOCK:
        run = ACTIVE_RUNS.get(message_id)
        return bool(run and run["cancel_requested"])


def list_active_runs() -> list[dict]:
    with ACTIVE_RUNS_LOCK:
        return [
            {key: run[key] for key in ("message_id", "conversation_id", "agent_id", "started_at")}
            for run in ACTIVE_RUNS.values()
            if not run["cancel_requested"]
        ]


def terminate_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        taskkill = shutil.which("taskkill")
        if taskkill:
            try:
                subprocess.run(
                    [taskkill, "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=8,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        try:
            process.terminate()
        except OSError:
            return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        try:
            process.kill()
        except OSError:
            pass


def stop_active_run(message_id: int) -> bool:
    run = active_run(message_id)
    if not run:
        return False
    with run['state_lock']:
        execution = run.get('execution')
        if execution and execution.done.is_set():
            return False
        run['cancel_requested'] = True
        try:
            if execution:
                execution.cancel()
        except Exception:
            logging.exception('Cancel intent persistence failed; process cancellation remains active')
    update_workbench_message(message_id, '已停止。', 'cancelled')
    return True


def stop_all_active_runs() -> int:
    with ACTIVE_RUNS_LOCK:
        message_ids = [
            message_id for message_id, run in ACTIVE_RUNS.items()
            if not run["cancel_requested"]
        ]
    stopped = 0
    for message_id in message_ids:
        try:
            if stop_active_run(message_id):
                stopped += 1
        except sqlite3.Error:
            # Cancellation has already been signalled before persistence errors.
            stopped += 1
    return stopped


def prepare_execution_session(message_id, agent_id, executable, environment):
    """One-way authority selection, frozen for this invocation. Never dual-write."""
    execution = active_run(message_id)['execution']
    resolver = SESSION_CONFIGURATION_RESOLVER
    if resolver is None:
        execution.session_configuration = None
        if agent_id == 'grok':
            return execution.select_legacy_session('grok', GROK_SESSION_KEY)
        return execution.select_legacy_session('codex' if agent_id == 'codex' else 'dsh',
                                               None if agent_id == 'codex' else DSH_SESSION_KEY)
    try:
        config = resolver(agent_id, execution.working_directory, executable, environment)
        if config is None or not config.evidence_reference:
            raise RuntimeError('Session namespace evidence missing; refusing production cutover')
        expected_adapter = 'codex' if agent_id == 'codex' else 'grok' if agent_id == 'grok' else 'dsh'
        if config.identity.adapter_id != expected_adapter:
            raise RuntimeError('Session adapter mismatch')
    except Exception as error:
        failure = ExecutionAdmissionError(error)
        execution.dispatch_error = str(failure)
        raise failure from error
    execution.session_configuration = config
    return execution.claim_session(config.identity)


def run_managed_process(message_id: int, command: list[str], **kwargs) -> subprocess.CompletedProcess:
    run = active_run(message_id)
    if not run or not run.get('execution'):
        raise RuntimeError('CLI requires an admitted execution domain')
    execution = run['execution']
    metadata = kwargs.pop('diagnostic_metadata', None) or {}
    kwargs.pop('text', None)
    kwargs.pop('encoding', None)
    kwargs.pop('errors', None)
    if kwargs.pop('shell', False):
        raise ValueError('Managed execution does not allow shell=True')
    command = list(command)
    if not Path(command[0]).is_absolute():
        resolved = shutil.which(command[0])
        if not resolved:
            raise FileNotFoundError('Launch executable not found')
        command[0] = resolved
    snapshot = dict(agent_id=run['agent_id'], executable=command[0],
                    working_directory=str(execution.working_directory), workspace_version=0,
                    launch_mode=metadata.get('session_mode', 'resume' if '--session-id' in command or 'resume' in command else 'new'))
    if run['agent_id'] == 'codex':
        snapshot['sandbox'] = command[command.index('--sandbox') + 1] if '--sandbox' in command else 'profile-default'
    elif run['agent_id'] == 'grok':
        snapshot['permission_mode'] = command[command.index('--permission-mode') + 1]
        snapshot['tools_denied'] = '--deny' in command and command[command.index('--deny') + 1] == '*'
    else:
        snapshot['profile'] = command[command.index('--profile') + 1]
        patch = command[command.index('--patch') + 1]
        snapshot['configuration_id'] = hashlib.sha256(patch.encode('utf-8')).hexdigest()
    config = getattr(execution, 'session_configuration', None)
    if config is not None:
        try:
            config.check_launch(command, kwargs.get('cwd'), kwargs.get('env', os.environ))
        except Exception:
            execution.dispatch_error = 'Resolved backend configuration changed before startup'
            raise
        snapshot['configuration_id'] = config.identity.config_fingerprint
        snapshot['session_identity'] = dict(zip(
            ('adapter_id','backend_scope','config_fingerprint','workspace_id','workspace_version'),
            config.identity.values()))
    result = execution.launch(command, snapshot=snapshot, **kwargs)
    if config is not None:
        validation = execution.observe_session(config.identity.adapter_id,
            result.stdout.decode('utf-8', errors='replace'),
            normal_completion=result.outcome == 'completed' and result.returncode == 0)
        try:
            config.check_launch(command, kwargs.get('cwd'), kwargs.get('env', os.environ))
        except Exception:
            from execution_tracking import SessionValidation
            execution.session_validation = SessionValidation('unverified', validation.actual_id, False, validation.observed_ids)
            execution.dispatch_error = 'Resolved backend configuration changed during execution'
            raise
        if result.outcome == 'completed' and validation.status != 'verified':
            execution.dispatch_error = 'Session ID missing or inconsistent'
            raise RuntimeError('Session ID missing or inconsistent; binding retained for review')
    if result.outcome == 'cancelled':
        raise AgentRunCancelled()
    if result.outcome == 'timeout':
        raise subprocess.TimeoutExpired(command, kwargs.get('timeout'))
    if result.outcome != 'completed' or result.exit_evidence != 'confirmed':
        raise RuntimeError('Managed process failed: ' + (result.error_type or result.outcome))
    return subprocess.CompletedProcess(command, result.returncode,
        result.stdout.decode('utf-8', errors='replace'), result.stderr.decode('utf-8', errors='replace'))


def touch_agent_last_seen(agent_id: str) -> None:
    connection = connect_workbench(timeout=10)
    try:
        with connection:
            connection.execute(
                "UPDATE agents SET last_seen = ? WHERE agent_id = ?",
                (utc_now(), agent_id),
            )
    finally:
        connection.close()


def truncate_head_tail(content: str, limit: int) -> str:
    """Keep the head and tail of an over-long message and mark the omitted middle.

    ``limit`` counts the kept body characters only; the omission note is added on top.
    """
    total = len(content)
    head_len = limit // 2
    tail_len = limit - head_len  # odd limit gives the tail one extra character
    omitted = total - limit
    return (
        content[:head_len]
        + f"\n…（中间省略 {omitted} 字符；原文共 {total} 字符）\n"
        + content[-tail_len:]
    )


def build_agent_prompt(
    conversation_id: str,
    agent_id: str,
    user_message_id: int,
    prompt: str,
    working_directory: Path,
) -> str:
    """Add shared context and the active workspace policy to the agent request."""
    with closing(open_database()) as connection:
        workbench_rows = connection.execute(
            """SELECT m.sender_id, COALESCE(sender.name, m.sender_id) AS sender_name,
                      m.recipient_id, COALESCE(recipient.name, m.recipient_id) AS recipient_name,
                      m.content, m.created_at, m.id, 'workbench' AS source
               FROM workbench_messages AS m
               LEFT JOIN agents AS sender ON sender.agent_id = m.sender_id
               LEFT JOIN agents AS recipient ON recipient.agent_id = m.recipient_id
               WHERE m.conversation_id = ? AND m.id < ? AND m.status = 'completed'
               ORDER BY m.id DESC LIMIT ?""",
            (conversation_id, user_message_id, AGENT_CONTEXT_MAX_MESSAGES),
        ).fetchall()
        bridge_rows = connection.execute(
            """SELECT m.sender_id, COALESCE(sender.name, m.sender_id) AS sender_name,
                      m.recipient_id, COALESCE(recipient.name, m.recipient_id) AS recipient_name,
                      m.content, m.created_at, m.id, 'bridge' AS source
               FROM messages AS m
               LEFT JOIN agents AS sender ON sender.agent_id = m.sender_id
               LEFT JOIN agents AS recipient ON recipient.agent_id = m.recipient_id
               WHERE m.conversation_id = ?
               ORDER BY m.id DESC LIMIT ?""",
            (conversation_id, AGENT_CONTEXT_MAX_MESSAGES),
        ).fetchall()

    history = [dict(row) for row in workbench_rows]
    history.extend(dict(row) for row in bridge_rows)
    history = [
        item for item in history
        if item["source"] == "bridge"
        or (item["sender_id"] != agent_id
            and not (item["sender_id"] == "user"
                     and item["recipient_id"] in {agent_id, "all"}))
    ]
    history.sort(key=lambda item: (item["created_at"], item["source"], item["id"]))
    history = history[-AGENT_CONTEXT_MAX_MESSAGES:]
    transcript = []
    for item in history:
        content = item["content"]
        if len(content) > AGENT_CONTEXT_MAX_MESSAGE_CHARS:
            content = truncate_head_tail(content, AGENT_CONTEXT_MAX_MESSAGE_CHARS)
        transcript.append(
            f"[{item['sender_name']} → {item['recipient_name']}]\n{content}"
        )
    shared_context = ""
    if transcript:
        shared_context = (
            "以下是当前工作台共享对话中其他 Agent 或消息桥的近期上下文。"
            "请将其作为背景，不要重复回答历史问题。\n\n"
            + "\n\n".join(transcript)
            + "\n\n"
        )
    workspace_context = f"当前工作目录：{working_directory}\n\n"
    project_entry = (working_directory / "AGENTS.md").read_text(encoding="utf-8")
    entry_context = (
        "以下是项目入口（来源：AGENTS.md 全文）\n"
        f"入口所在项目目录：{working_directory}\n\n"
        + project_entry
        + "\n\n"
    )
    spec_index = (working_directory / "specs" / "README.md").read_text(encoding="utf-8")
    spec_context = (
        "以下是项目规范索引（来源：specs/README.md 全文）\n"
        f"索引所在项目目录：{working_directory}\n"
        "索引内列出的规范路径均相对于该项目的 specs/ 目录，"
        "如 AGENT_DISCUSSION_SPEC.md 对应 specs/AGENT_DISCUSSION_SPEC.md。\n"
        "操作前按适用场景判断规范归属（must_follow/not_applicable/uncertain）"
        "并读取 must_follow 规范正文；场景变化时复核；所需正文不可用时补读；"
        "读取失败如实说明；交付时在 handoff.evidence 简要记录适用规范 id、"
        "版本、路径、读取情况与失败项（普通聊天无 handoff 时在可读答复中简记）。\n\n"
        + spec_index
        + "\n\n"
    )
    return entry_context + spec_context + workspace_context + shared_context + "当前用户消息：\n" + prompt


@structured_worker
def run_codex(
    conversation_id: str,
    prompt: str,
    assistant_message_id: int,
    user_message_id: int,
) -> WorkerResult:
    try:
        touch_agent_last_seen("codex")
        working_directory = active_run(assistant_message_id)['execution'].working_directory
        codex_command = resolve_codex_command()
        if not codex_command:
            raise RuntimeError(
                "找不到 Codex CLI。请确认已安装 Codex；也可以设置 CODEX_CLI 为 codex.exe 的完整路径。"
            )

        environment = os.environ.copy()
        thread_id = prepare_execution_session(assistant_message_id, 'codex', codex_command, environment)
        agent_prompt = build_agent_prompt(
            conversation_id, "codex", user_message_id, prompt, working_directory
        )
        with tempfile.TemporaryDirectory(prefix="agent-workbench-") as temporary_directory:
            answer_path = Path(temporary_directory) / "codex-answer.txt"
            sandbox_args = ["--sandbox", "read-only"] if getattr(COLLAB_CONTEXT, "final_delivery", False) else []
            if thread_id:
                command = [
                    codex_command, "exec", *sandbox_args,
                    "--cd", str(working_directory), "resume",
                    "--json", "--skip-git-repo-check", "--output-last-message", str(answer_path),
                    thread_id, "-",
                ]
            else:
                command = [
                    codex_command, "exec", *sandbox_args,
                    "--json", "--skip-git-repo-check",
                    "--cd", str(working_directory),
                    "--output-last-message", str(answer_path), "-",
                ]

            result = run_managed_process(
                assistant_message_id,
                command,
                cwd=working_directory,
                env=environment,
                input=agent_prompt,
                diagnostic_metadata={
                    "agent_id": "codex",
                    "transport": "stdin",
                    "session_mode": "resume" if thread_id else "new",
                    "prompt_utf8_bytes": len(agent_prompt.encode("utf-8")),
                    # Windows serialization length in UTF-16 units, excluding NUL.
                    "command_utf16_units": len(
                        subprocess.list2cmdline(command).encode("utf-16-le")
                    ) // 2,
                },
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=AGENT_CODEX_TIMEOUT_SECONDS,
            )
            if result.returncode != 0:
                diagnostic = (result.stderr or result.stdout or "Codex CLI 执行失败").strip()
                raise RuntimeError(diagnostic)

            if not thread_id:
                for line in result.stdout.splitlines():
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "thread.started":
                        thread_id = event.get("thread_id")
                        break
                if not thread_id:
                    raise RuntimeError("Codex 已返回，但没有找到新会话的 thread_id。")
                if active_run(assistant_message_id)['execution'].session_claim is None:
                    save_codex_thread_id(conversation_id, thread_id)

            answer = answer_path.read_text(encoding="utf-8").strip() if answer_path.exists() else ""
            if not answer:
                raise RuntimeError("Codex 没有返回可显示的最终回复。")
            return worker_reply(assistant_message_id, answer, "completed")
    except AgentRunCancelled:
        return worker_reply(assistant_message_id, "已停止。", "cancelled")
    except subprocess.TimeoutExpired:
        if is_run_cancelled(assistant_message_id):
            return worker_reply(assistant_message_id, "已停止。", "cancelled")
        else:
            return worker_reply(assistant_message_id, f"Codex 执行超时（{AGENT_CODEX_TIMEOUT_SECONDS} 秒）。可能已有文件修改，未自动回退。", "failed")
    except Exception as error:
        if is_run_cancelled(assistant_message_id):
            return worker_reply(assistant_message_id, "已停止。", "cancelled")
        else:
            message = format_execution_error(error)
            return worker_reply(assistant_message_id, "调用 Codex 失败：" + message[:4000], "failed")


def resolve_dsh_desktop_runtime(command: str) -> tuple[str, str] | None:
    """Find the Electron-bundled DSH CLI behind the DeepSeek Harness desktop shim."""
    if os.name != "nt" or Path(command).suffix.lower() not in {".cmd", ".bat"}:
        return None

    command_path = Path(command)
    try:
        # The desktop shim lives at resources/runtime/cli/bin/dsh.cmd.
        resources_dir = command_path.parents[3]
    except IndexError:
        return None

    electron_exe = resources_dir.parent / "DeepSeek Harness.exe"
    app_asar = resources_dir / "app.asar"
    if not electron_exe.is_file() or not app_asar.is_file():
        return None

    # Electron resolves this path from app.asar. It is intentionally not checked
    # with Path.is_file(), because the entry is inside the ASAR archive.
    cli_entry = str(
        resources_dir
        / "app.asar"
        / "dsh"
        / "node_modules"
        / "@deepseek-ai"
        / "dsh"
        / "lib"
        / "bin.js"
    )
    return str(electron_exe), cli_entry


def build_dsh_command(command: str, arguments: list[str]) -> tuple[list[str], bool, bool]:
    """Build a DSH command, bypassing the desktop-host shim when available."""
    desktop_runtime = resolve_dsh_desktop_runtime(command)
    if desktop_runtime:
        electron_exe, cli_entry = desktop_runtime
        return [electron_exe, "--expose-internals", cli_entry, *arguments], False, True
    if os.name == "nt" and Path(command).suffix.lower() in {".cmd", ".bat"}:
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", command, *arguments], False, False
    return [command, *arguments], False, False


@structured_worker
def run_deepseek_harness(
    conversation_id: str,
    prompt: str,
    assistant_message_id: int,
    user_message_id: int,
) -> WorkerResult:
    try:
        touch_agent_last_seen("deepseek-harness")
        working_directory = active_run(assistant_message_id)['execution'].working_directory
        dsh_command = resolve_dsh_command()
        if not dsh_command:
            raise RuntimeError(
                "找不到 dsh CLI。请在 DeepSeek Harness 桌面端的“Manage dsh Command”中安装命令，"
                "重新打开 PowerShell 并重启工作台；也可设置 DSH_CLI 为 dsh 可执行文件的完整路径。"
            )
        if not DSH_AUTH_PATCH.is_file():
            raise RuntimeError(f"找不到 DSH headless 登录配置：{DSH_AUTH_PATCH}")

        # Build the launch configuration first; resolve its fixed ID exactly once below.
        agent_prompt = build_agent_prompt(
            conversation_id, "deepseek-harness", user_message_id, prompt, working_directory
        )
        arguments = [
            "--profile", DSH_PROFILE,
            "--patch", str(DSH_AUTH_PATCH),
            "--json",
        ]
        arguments.append("-")
        command, use_shell, use_electron_as_node = build_dsh_command(dsh_command, arguments)
        environment = os.environ.copy()
        if use_electron_as_node:
            environment["ELECTRON_RUN_AS_NODE"] = "1"
        session_id = prepare_execution_session(assistant_message_id, 'deepseek-harness', command[0], environment)
        if session_id:
            if not all(char.isalnum() or char in '._-' for char in session_id):
                raise RuntimeError('Invalid DSH resume ID')
            command[-1:-1] = ['--session-id', session_id]
        result = run_managed_process(
            assistant_message_id,
            command,
            cwd=working_directory,
            env=environment,
            input=agent_prompt,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=AGENT_DEEPSEEK_TIMEOUT_SECONDS,
            shell=use_shell,
        )

        events: list[dict] = []
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)

        if not session_id:
            for event in events:
                if event.get("type") == "session" and isinstance(event.get("sessionId"), str):
                    session_id = event["sessionId"]
                    break
            if session_id:
                if active_run(assistant_message_id)['execution'].session_claim is None:
                    save_agent_session_id(conversation_id, DSH_SESSION_KEY, session_id)

        if result.returncode != 0:
            diagnostic = (result.stderr or "").strip()
            error_events = [event.get("message") for event in events if event.get("type") == "error"]
            diagnostic = diagnostic or next((str(message) for message in error_events if message), "DSH CLI 执行失败")
            raise RuntimeError(diagnostic[:4000])

        answer = next(
            (event.get("text", "") for event in reversed(events) if event.get("type") == "final"),
            "",
        ).strip()
        if not answer:
            raise RuntimeError("DeepSeek Harness 已退出，但没有返回可显示的最终回复。")
        return worker_reply(assistant_message_id, answer, "completed")
    except AgentRunCancelled:
        return worker_reply(assistant_message_id, "已停止。", "cancelled")
    except subprocess.TimeoutExpired:
        if is_run_cancelled(assistant_message_id):
            return worker_reply(assistant_message_id, "已停止。", "cancelled")
        else:
            return worker_reply(assistant_message_id, f"DeepSeek Harness 执行超时（{AGENT_DEEPSEEK_TIMEOUT_SECONDS} 秒）。可能已有文件修改，未自动回退。", "failed")
    except Exception as error:
        if is_run_cancelled(assistant_message_id):
            return worker_reply(assistant_message_id, "已停止。", "cancelled")
        else:
            message = format_execution_error(error)
            return worker_reply(assistant_message_id, "调用 DeepSeek Harness 失败：" + message[:4000], "failed")


def build_grok_command(grok_command: str, prompt_path: Path, working_directory: Path,
                       session_id: str | None, final_delivery: bool) -> list[str]:
    """Explicit tool approvals; final denies tools, not a Windows OS sandbox."""
    return grok_command_args(grok_command, prompt_path, working_directory, session_id, final_delivery)


def parse_grok_output(stdout: str) -> tuple[str, str | None, list[str], list[str]]:
    return grok_parse_output(stdout)


@structured_worker
def run_grok(
    conversation_id: str,
    prompt: str,
    assistant_message_id: int,
    user_message_id: int,
) -> WorkerResult:
    try:
        touch_agent_last_seen("grok")
        working_directory = active_run(assistant_message_id)['execution'].working_directory
        grok_command = resolve_grok_command()
        if not grok_command:
            raise RuntimeError(
                "找不到 Grok CLI。请安装 Grok Build（grok）并登录；也可设置 GROK_CLI 为 grok.exe 的完整路径。"
            )
        agent_prompt = build_agent_prompt(
            conversation_id, "grok", user_message_id, prompt, working_directory
        )
        environment = os.environ.copy()
        session_id = prepare_execution_session(assistant_message_id, 'grok', grok_command, environment)
        if session_id and not all(char.isalnum() or char in '._-' for char in session_id):
            raise RuntimeError('Invalid Grok resume ID')
        final_delivery = getattr(COLLAB_CONTEXT, "final_delivery", False)
        with tempfile.TemporaryDirectory(prefix="agent-workbench-") as temporary_directory:
            prompt_path = Path(temporary_directory) / "grok-prompt.txt"
            prompt_path.write_bytes(agent_prompt.encode("utf-8"))
            command = build_grok_command(grok_command, prompt_path, working_directory,
                                         session_id, final_delivery)
            result = run_managed_process(
                assistant_message_id,
                command,
                cwd=working_directory,
                env=environment,
                diagnostic_metadata={
                    "agent_id": "grok",
                    "transport": "prompt-file",
                    "session_mode": "resume" if session_id else "new",
                },
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=AGENT_GROK_TIMEOUT_SECONDS,
            )
        answer, new_session_id, error_messages, permission_notes = parse_grok_output(result.stdout)
        if session_id and new_session_id != session_id:
            error_messages.append('Grok resumed session ID mismatch')
        if result.returncode != 0 or error_messages:
            diagnostic = grok_diagnostic((result.stderr or "").strip())
            diagnostic = next(iter(error_messages), "") or diagnostic or "Grok CLI 执行失败"
            if permission_notes:
                diagnostic += '\n' + '\n'.join(permission_notes)
            raise RuntimeError(diagnostic[:4000])
        if not answer:
            raise RuntimeError("Grok 已退出，但没有返回可显示的最终回复。")
        if is_run_cancelled(assistant_message_id):
            raise AgentRunCancelled()
        if not session_id and new_session_id:
            run = active_run(assistant_message_id)
            with run['state_lock']:
                if run['cancel_requested']:
                    raise AgentRunCancelled()
                if run['execution'].session_claim is None:
                    save_agent_session_id(conversation_id, GROK_SESSION_KEY, new_session_id)
        if permission_notes:
            answer = ("[工作台提示] CLI 诊断（已截断并脱敏）：\n"
                      + "\n".join(permission_notes) + '\n\n' + answer)
        return worker_reply(assistant_message_id, answer, "completed")
    except AgentRunCancelled:
        return worker_reply(assistant_message_id, "已停止。", "cancelled")
    except subprocess.TimeoutExpired:
        if is_run_cancelled(assistant_message_id):
            return worker_reply(assistant_message_id, "已停止。", "cancelled")
        else:
            return worker_reply(assistant_message_id, f"Grok 执行超时（{AGENT_GROK_TIMEOUT_SECONDS} 秒）。可能已有文件修改，未自动回退。", "failed")
    except Exception as error:
        if is_run_cancelled(assistant_message_id):
            return worker_reply(assistant_message_id, "已停止。", "cancelled")
        else:
            message = format_execution_error(error)
            return worker_reply(assistant_message_id, "调用 Grok 失败：" + message[:4000], "failed")


def agent_worker(agent_id: str):
    adapter = default_registry().require(agent_id).adapter
    return {'codex': run_codex, 'dsh': run_deepseek_harness, 'grok': run_grok}[adapter]


def run_agent_task(
    agent_id: str,
    conversation_id: str,
    prompt: str,
    assistant_message_id: int,
    user_message_id: int,
) -> None:
    previous_validation = getattr(COLLAB_CONTEXT, "validating_entry", False)
    previous_received = getattr(COLLAB_CONTEXT, "entry_reply_received", False)
    owns_execution = False
    pending_kick = None
    try:
        _recipients, collaboration = route_message(prompt)
        COLLAB_CONTEXT.validating_entry = collaboration
        COLLAB_CONTEXT.entry_reply_received = False
        if collaboration:
            with closing(connect_workbench(timeout=10)) as connection:
                if connection.execute("SELECT 1 FROM collaboration_entry_results WHERE message_id=?", (assistant_message_id,)).fetchone():
                    return  # Already processed: never invoke the Agent again.
                row = connection.execute("SELECT status FROM workbench_messages WHERE id=?", (assistant_message_id,)).fetchone()
                if not row or row[0] != "pending":
                    return
        with closing(connect_workbench()) as check:
            row = check.execute('SELECT status FROM workbench_messages WHERE id=?', (assistant_message_id,)).fetchone()
            if not row or row[0] != 'pending':
                return
        run = active_run(assistant_message_id)
        if not run:
            return  # A concurrent completion may already have unregistered it.
        with run['state_lock']:
            if run['claimed']:
                return
            owns_execution = True
            begin_execution(assistant_message_id, source_kind='collaboration' if collaboration else 'workbench',
                            job_id=f'entry-{assistant_message_id}' if collaboration else None,
                            call_id='entry' if collaboration else None)
        worker = agent_worker(agent_id)
        reply = worker(conversation_id, entry_prompt(prompt) if collaboration else prompt,
               assistant_message_id, user_message_id)
        if not isinstance(reply, WorkerResult):
            raise RuntimeError("Worker returned no structured result")
        if collaboration:
            # Persist validated entry material; scheduling is a later stage.
            with closing(connect_workbench(timeout=10)) as connection:
                row = connection.execute(
                    "SELECT content, status FROM workbench_messages WHERE id = ?",
                    (assistant_message_id,),
                ).fetchone()
            if row and row[1] == "pending" and COLLAB_CONTEXT.entry_reply_received:
                try:
                    _readable, payload = parse_entry_response(row[0])
                except ValueError as error:
                    fail_entry_message(assistant_message_id, "protocol incompatible", error)
                else:
                    # Stop and commit share this lock; DB state is checked under
                    # the write transaction as well. A late result cannot undo stop.
                    with run['state_lock']:
                        if run["cancel_requested"]:
                            return
                        with closing(connect_workbench(timeout=10)) as connection, connection:
                            connection.execute("BEGIN IMMEDIATE")
                            updated = connection.execute("UPDATE workbench_messages SET status='completed' WHERE id=? AND status='pending'",
                                                         (assistant_message_id,)).rowcount
                            if not updated:
                                return
                            connection.execute("INSERT INTO collaboration_entry_results VALUES (?, ?, ?)",
                                (assistant_message_id, conversation_id, json.dumps(payload, ensure_ascii=False)))
                    if payload["status"] == "completed" and payload["plan"]["tasks"] == []:
                        return
                    job_id = f"entry-{assistant_message_id}"
                    service = None
                    try:
                        service = collaboration_service()
                        service.register_entry(job_id,conversation_id,agent_id,prompt,payload,row[0])
                        if is_run_cancelled(assistant_message_id):
                            service.store.stop(job_id)
                        else:
                            pending_kick = (service, job_id)
                    except Exception as error:
                        if service is not None:
                            try:
                                with service.store.transaction() as db:
                                    db.execute("UPDATE collab_lifecycle SET phase='failed',reason=? WHERE job_id=? AND phase NOT IN ('completed','quiescing')",
                                               ("registration/dispatch failed: " + str(error),job_id))
                            except Exception:
                                logging.exception("Could not persist collaboration registration failure")
                        fail_entry_message(assistant_message_id, "registration/dispatch failed", error)
            elif row and row[1] == "pending":
                fail_entry_message(assistant_message_id, "transport failed", "worker returned without a successful reply")
    except Exception as error:
        fail_entry_message(assistant_message_id, "persistence failed", error)
    finally:
        COLLAB_CONTEXT.validating_entry = previous_validation
        COLLAB_CONTEXT.entry_reply_received = previous_received
        if owns_execution:
            allowed = finish_execution(assistant_message_id)
            unregister_active_run(assistant_message_id)
            if pending_kick:
                service, job_id = pending_kick
                if allowed and not run['cancel_requested']:
                    service.kick(job_id)
                else:
                    with service.store.transaction() as db:
                        db.execute("UPDATE collab_lifecycle SET phase='failed',reason='Execution domain not released' WHERE job_id=? AND phase NOT IN ('completed','quiescing','stopped')", (job_id,))


def fail_entry_message(message_id, stage, error):
    """Preserve the Agent's raw reply and never overwrite cancellation."""
    logging.error("Entry %s: %s: %s", message_id, stage, error)
    try:
        run = active_run(message_id)
        lock = run['state_lock'] if run else threading.RLock()
        with lock:
            if run and run['cancel_requested']:
                return
            with closing(connect_workbench(timeout=10)) as db, db:
                db.execute("UPDATE workbench_messages SET status='failed',content=content || ? WHERE id=? AND status IN ('pending','completed')",
                           ("\n\n[Collaboration " + stage + "] " + format_execution_error(error)[:4000],message_id))
    except Exception:
        logging.exception("Could not persist entry failure for message %s", message_id)


class ChatHandler(BaseHTTPRequestHandler):
    server_version = "AgentWorkbench/0.1"

    def send_bytes(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_bytes(status, "application/json; charset=utf-8", body)

    def capability_host_allowed(self) -> bool:
        port = self.server.server_port
        return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def capability_snapshot(self, conversation_id: str) -> dict:
        available = cli_availability()
        return {
            "probe": CAPABILITY_PROBES.snapshot(resolve_working_directory(conversation_id)),
            "agent_configuration": [
                {"id": "codex", "name": "Codex", "configuration": "普通：用户权限 profile（未显式传 --sandbox）；最终交付：--sandbox read-only",
                 "cli_found": available["codex_cli_available"]},
                {"id": "deepseek-harness", "name": "DeepSeek Harness", "configuration": f"--profile {DSH_PROFILE}; 未显式传入 sandbox 参数，其他策略未知",
                 "cli_found": available["dsh_cli_available"]},
                {"id": "grok", "name": "Grok", "configuration": "普通：auto，工具可能被拒绝、不等待人工批准；最终交付：dontAsk + deny全部工具（非Windows OS沙箱）",
                 "cli_found": available["grok_cli_available"]},
            ],
        }

    def do_capability_probe(self) -> None:
        host = self.headers.get("Host", "")
        if (not self.capability_host_allowed()
                or self.headers.get("Origin") != "http://" + host
                or not secrets.compare_digest(self.headers.get("X-Capability-Token", ""), CAPABILITY_TOKEN)):
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
            directory = resolve_working_directory(conversation_id)
        except (ValueError, UnicodeError) as error:
            self.send_json(400, {"error": str(error)})
            return
        except (sqlite3.Error, OSError):
            self.send_json(503, {"error": "Working directory unavailable"})
            return
        try:
            CAPABILITY_PROBES.start(directory)
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
                store = collaboration_service().store
                with store.transaction() as db:
                    ids = [r[0] for r in db.execute("SELECT job_id FROM collab_bindings WHERE conversation_id=? ORDER BY rowid DESC LIMIT 20", (conversation,))]
                tasks = [store.view(job_id, conversation) for job_id in ids]
                service = collaboration_service()
                workflow = service.git_workflow
                if workflow:
                    for task in tasks:
                        task['git_feature'] = workflow.snapshot(task['state']['job_id'])
                self.send_json(200, {"tasks": tasks})
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
                self.send_bytes(200, "text/html; charset=utf-8", PAGE.replace("__CAPABILITY_TOKEN__", CAPABILITY_TOKEN).encode("utf-8"))
                return
            if parsed.path == "/api/agents":
                self.send_json(200, {
                    "agents": fetch_agents(),
                    **cli_availability(),
                })
                return
            if parsed.path == "/api/conversations":
                self.send_json(200, {"conversations": fetch_conversations()})
                return
            if parsed.path == "/api/settings":
                params = parse_qs(parsed.query)
                conversation_id = params.get("conversation_id", ["default"])[0]
                if not conversation_id or len(conversation_id) > 200:
                    self.send_json(400, {"error": "conversation_id 不能为空且不能超过 200 个字符"})
                    return
                self.send_json(200, {
                    "conversation_id": conversation_id,
                    "working_directory": get_working_directory_text(conversation_id),
                })
                return
            if parsed.path == "/api/runs":
                self.send_json(200, {"runs": list_active_runs()})
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
                limit = max(1, min(limit, MAX_MESSAGES))
                self.send_json(200, {
                    "conversation_id": conversation_id,
                    "messages": fetch_messages(conversation_id, limit),
                })
                return
            self.send_json(404, {"error": "Not found"})
        except (sqlite3.Error, OSError) as error:
            self.send_json(503, {"error": str(error)})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == '/api/git/action':
            handle_git_action(self, collaboration_service(), CAPABILITY_TOKEN)
            return
        if parsed.path == "/api/collaboration/action":
            service = collaboration_service()
            handle_action_request(self, service.store, CAPABILITY_TOKEN, service.action_changed)
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
                normalized_directory = normalize_working_directory(working_directory)
            except ValueError as error:
                self.send_json(400, {"error": str(error)})
                return
            with ACTIVE_RUNS_LOCK:
                if any(
                    run["conversation_id"] == conversation_id
                    for run in ACTIVE_RUNS.values()
                ):
                    self.send_json(409, {"error": "该对话有 Agent 正在运行，请等任务完成或停止后再更改工作目录。"})
                    return
                try:
                    changed = save_working_directory(conversation_id, normalized_directory)
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
                service = collaboration_service()
                with service.store.transaction() as db:
                    bindings = list(db.execute("SELECT job_id,conversation_id FROM collab_bindings"))
                for job_id, conversation in bindings:
                    with ACTIVE_RUNS_LOCK:
                        matching = payload.get("message_id") in COLLAB_MESSAGES.get(job_id,set())
                    if payload.get("all") is True or matching:
                        if service.store.snapshot(job_id)["phase"] not in {"completed","stopped"}:
                            service.store.operate(job_id,conversation,secrets.token_hex(16),"stop")
                            service.action_changed(job_id)
                if payload.get("all") is True:
                    stopped = stop_all_active_runs()
                    self.send_json(200, {"stopped": stopped})
                    return
                message_id = payload.get("message_id")
                if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id < 1:
                    self.send_json(400, {"error": "message_id 必须是正整数，或指定 all=true"})
                    return
                if not stop_active_run(message_id):
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
        if len(content) > MAX_MESSAGE_LENGTH:
            self.send_json(400, {"error": f"消息不能超过 {MAX_MESSAGE_LENGTH} 个字符"})
            return
        if not conversation_id or len(conversation_id) > 200:
            self.send_json(400, {"error": "conversation_id 不能为空且不能超过 200 个字符"})
            return
        recipients, agent_prompts = resolve_message_recipients(content)
        try:
            recipients = requested_recipients(recipients, payload.get('recipients'), route_message(content)[1])
            agent_prompts = {key: agent_prompts[key] for key in recipients}
        except ValueError as error:
            self.send_json(400, {'error': str(error)})
            return
        if not recipients or not agent_prompts:
            self.send_json(400, {"error": "请在 Agent 名称后输入要发送的消息"})
            return

        if "grok" in recipients and not resolve_grok_command():
            if len(recipients) == 1 or payload.get('recipients') is not None:
                self.send_json(503, {
                    "error": "Grok CLI 不可用，请刷新可用名单后重试；也可设置 GROK_CLI 为 grok.exe 的完整路径。"
                })
                return
            # A missing Grok never blocks a broadcast to the other Agents.
            recipients = [agent_id for agent_id in recipients if agent_id != "grok"]
            agent_prompts.pop("grok", None)

        if "codex" in recipients and not resolve_codex_command():
            self.send_json(503, {
                "error": "找不到 Codex CLI。请确认已安装 Codex；也可以设置 CODEX_CLI 为 codex.exe 的完整路径。"
            })
            return
        if "deepseek-harness" in recipients:
            try:
                if not any(agent["agent_id"] == "deepseek-harness" for agent in fetch_agents()):
                    self.send_json(409, {"error": "DeepSeek Harness 尚未登记到 agent_bridge。"})
                    return
            except sqlite3.Error as error:
                self.send_json(503, {"error": str(error)})
                return
            if not resolve_dsh_command():
                self.send_json(503, {
                    "error": "找不到 dsh CLI。请在 DeepSeek Harness 桌面端的“Manage dsh Command”中安装命令，重新打开 PowerShell 并重启工作台。"
                })
                return

        with ACTIVE_RUNS_LOCK:
            active_run = next(
                (
                    run for run in ACTIVE_RUNS.values()
                    if run["conversation_id"] == conversation_id
                    and run["agent_id"] in recipients
                ),
                None,
            )
        connection = connect_workbench(timeout=10)
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
                label = AGENT_LABELS.get(pending[0], "DeepSeek Harness")
                self.send_json(409, {
                    "error": label + " 正在处理这段对话。请等待它完成，或只 @ 另一个 Agent。"
                })
                return
            if active_run:
                connection.rollback()
                label = AGENT_LABELS.get(active_run["agent_id"], "DeepSeek Harness")
                self.send_json(409, {"error": label + " 的上一条任务尚未完全结束，请稍后重试。"})
                return

            created_at = utc_now()
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
            register_active_run(assistant_message_ids[agent_id], conversation_id, agent_id)

        self.send_json(202, {
            "conversation_id": conversation_id,
            "user_message_id": user_message_id,
            "assistant_message_ids": assistant_message_ids,
            "recipients": recipients,
            "status": "accepted",
        })
        for agent_id in recipients:
            threading.Thread(
                target=run_agent_task,
                args=(agent_id, conversation_id, agent_prompts[agent_id], assistant_message_ids[agent_id], user_message_id),
                daemon=True,
            ).start()

    def log_message(self, format: str, *args: object) -> None:
        print("[%s] %s" % (self.log_date_time_string(), format % args))


@contextmanager
def startup_server(port, *, allow_legacy=False):
    """Lock the database before binding or performing any persistent changes."""
    global DB_PATH, SERVICE_INSTANCE_ID
    with ServiceInstanceLock(DB_PATH) as instance:
        DB_PATH = instance.database_path
        SERVICE_INSTANCE_ID = uuid.uuid4().hex
        # Construct without binding so every failure path can close the socket.
        server = None
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), ChatHandler, bind_and_activate=False)
            server.server_bind()
            server.server_activate()
            import agent_bridge
            agent_bridge.DB_PATH = DB_PATH
            agent_bridge.initialize_db(allow_legacy=allow_legacy)
            initialize_workbench_tables()
            collaboration_service().store.interrupt_running()
            try:
                occupancy = read_occupancy_snapshot(DB_PATH, current_instance_id=SERVICE_INSTANCE_ID)
            except InspectionQueryError:
                # A failed diagnostic is not an empty/safe snapshot. Admission
                # remains transactionally checked on every execution request.
                logging.error('[查询失败] 启动占用摘要不可用；请检查数据库及迁移状态，未判定为无占用。')
            else:
                blocked = sum(item['blocked'] for item in occupancy)
                reviews = sum(item['classification'] == 'session_needs_validation' for item in occupancy)
                logging.log(logging.WARNING if blocked else logging.INFO,
                            '6A启动只读摘要：记录阻断域=%s，会话待校验=%s；退出情况未知；摘要不用于准入。',
                            blocked, reviews)
            # This snapshot is local diagnostics only, never an admission cache.
            yield server
        finally:
            SERVICE_INSTANCE_ID = None
            if server is not None:
                server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="启动本地 Agent 工作台并接入 Codex CLI 与 DeepSeek Harness")
    parser.add_argument("--port", type=int, default=8765, help="本地网页端口（默认：8765）")
    parser.add_argument("--allow-legacy-upgrade", action="store_true", help="Confirm all legacy writers are stopped before upgrading")
    args = parser.parse_args()
    with startup_server(args.port, allow_legacy=args.allow_legacy_upgrade) as server:
        serve(server, args.port)


def serve(server, port):
    print(f"Agent 工作台已启动：http://127.0.0.1:{port}")
    print(f"共享数据库：{DB_PATH}")
    print("按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭 Agent 工作台……")
    finally:
        stop_all_active_runs()


if __name__ == "__main__":
    main()
