"""Local multi-agent workbench backed by the agent_bridge SQLite database."""

import argparse
import json
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
from contextlib import closing
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


from capability_probe import ProbeManager


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
            })
        return dict(CLI_AVAILABILITY_CACHE[1])

PROJECT_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("AGENT_BRIDGE_DB", PROJECT_DIR / "agent_bridge.db")).expanduser()
DSH_AUTH_PATCH = Path(os.environ.get("DSH_HEADLESS_PATCH", PROJECT_DIR / "dsh-headless-auth.patch.yml")).expanduser()
MAX_MESSAGES = 1000
MAX_MESSAGE_LENGTH = 20_000
AGENT_IDS = ("codex", "deepseek-harness")
DSH_SESSION_KEY = "deepseek-harness-headless"


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
AGENT_CONTEXT_MAX_MESSAGE_CHARS = _env_positive_int("AGENT_CONTEXT_MAX_MESSAGE_CHARS", 3000)
MENTION_PATTERNS = {
    "codex": re.compile(r"(?<![A-Za-z0-9_@\\])@(?:codex|openai)(?![A-Za-z0-9_]|\.[A-Za-z])", re.IGNORECASE),
    "deepseek-harness": re.compile(
        r"(?<![A-Za-z0-9_@\\])@(?:deepseek\s+harness|deepseekharness|deepseek)(?![A-Za-z0-9_]|\.[A-Za-z])",
        re.IGNORECASE,
    ),
}
ALL_AGENTS_MENTION = re.compile(
    r"(?<![A-Za-z0-9_@\\])@(?:all|everyone|所有|全部)(?![A-Za-z0-9_]|\.[A-Za-z])",
    re.IGNORECASE,
)
CODE_SPAN_PATTERN = re.compile(r"```[\s\S]*?```|`[^`\r\n]*`")
ACTIVE_RUNS_LOCK = threading.RLock()
ACTIVE_RUNS: dict[int, dict] = {}


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
    """Route mentions and give each explicitly mentioned agent its own segment."""
    mention_text = CODE_SPAN_PATTERN.sub(lambda match: " " * len(match.group()), content)
    mentions = sorted(
        (match.start(), match.end(), agent_id)
        for agent_id, pattern in MENTION_PATTERNS.items()
        for match in pattern.finditer(mention_text)
    )
    if ALL_AGENTS_MENTION.search(mention_text) or not mentions:
        prompt = clean_message_prompt(content)
        return list(AGENT_IDS), {agent_id: prompt for agent_id in AGENT_IDS}

    prompts_by_agent: dict[str, list[str]] = {agent_id: [] for agent_id in AGENT_IDS}
    if content[:mentions[0][0]].strip():
        # Text-first style: each text segment belongs to the mention that follows it.
        cursor = 0
        for start, end, agent_id in mentions:
            segment = clean_message_prompt(content[cursor:start])
            if segment:
                prompts_by_agent[agent_id].append(segment)
            cursor = end
        trailing_text = clean_message_prompt(content[cursor:])
        if trailing_text:
            prompts_by_agent[mentions[-1][2]].append(trailing_text)
    else:
        # Mention-first style: each text segment belongs to the mention before it.
        for index, (_start, end, agent_id) in enumerate(mentions):
            next_start = mentions[index + 1][0] if index + 1 < len(mentions) else len(content)
            segment = clean_message_prompt(content[end:next_start])
            if segment:
                prompts_by_agent[agent_id].append(segment)

    prompts = {
        agent_id: "\n\n".join(agent_segments)
        for agent_id, agent_segments in prompts_by_agent.items()
        if agent_segments
    }
    recipients = [agent_id for agent_id in AGENT_IDS if agent_id in prompts]
    return recipients, prompts


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def initialize_workbench_tables() -> None:
    if not DB_PATH.is_file():
        raise FileNotFoundError(f"找不到桥接数据库：{DB_PATH}")
    connection = sqlite3.connect(DB_PATH, timeout=10)
    try:
        with connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workbench_sessions (
                    conversation_id TEXT PRIMARY KEY,
                    codex_thread_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS workbench_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    recipient_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'completed', 'failed', 'cancelled'))
                );

                CREATE INDEX IF NOT EXISTS idx_workbench_messages_conversation
                    ON workbench_messages(conversation_id, id);

                CREATE TABLE IF NOT EXISTS workbench_agent_sessions (
                    conversation_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (conversation_id, agent_id)
                );

                CREATE TABLE IF NOT EXISTS workbench_conversation_settings (
                    conversation_id TEXT PRIMARY KEY,
                    working_directory TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            schema_row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'workbench_messages'"
            ).fetchone()
            if schema_row and "'cancelled'" not in (schema_row[0] or ""):
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("DROP INDEX IF EXISTS idx_workbench_messages_conversation")
                connection.execute("ALTER TABLE workbench_messages RENAME TO workbench_messages_before_cancel")
                connection.execute(
                    """CREATE TABLE workbench_messages (
                           id INTEGER PRIMARY KEY AUTOINCREMENT,
                           conversation_id TEXT NOT NULL,
                           sender_id TEXT NOT NULL,
                           recipient_id TEXT NOT NULL,
                           content TEXT NOT NULL,
                           created_at TEXT NOT NULL,
                           status TEXT NOT NULL CHECK (status IN ('pending', 'completed', 'failed', 'cancelled'))
                       )"""
                )
                connection.execute(
                    """INSERT INTO workbench_messages
                       (id, conversation_id, sender_id, recipient_id, content, created_at, status)
                       SELECT id, conversation_id, sender_id, recipient_id, content, created_at, status
                       FROM workbench_messages_before_cancel"""
                )
                connection.execute("DROP TABLE workbench_messages_before_cancel")
                connection.execute(
                    "CREATE INDEX idx_workbench_messages_conversation ON workbench_messages(conversation_id, id)"
                )
            connection.execute(
                """UPDATE workbench_messages
                   SET content = '工作台重新启动时，未完成任务已停止。', status = 'cancelled'
                   WHERE status = 'pending'"""
            )
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
    .section-title { display:flex;justify-content:space-between;align-items:center;padding:0 8px;margin:12px 0 10px;color:#c2c9d5;font-size:11px;font-weight:700;letter-spacing:.12em;text-transform:uppercase; }
    .count { color:var(--muted);font-size:11px;font-weight:500;letter-spacing:0; }
    #agents { display:grid;gap:9px; }
    .agent { background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px; }
    .agent-head { display:flex;align-items:center;gap:10px; }
    .avatar { flex:none;width:32px;height:32px;border-radius:10px;display:grid;place-items:center;font-weight:750;color:#12151b;background:#a8c7fa; }
    .avatar.deepseek { background:#b8ead5; }
    .agent-name { font-weight:650; }
    .agent-vendor { color:var(--muted);font-size:11px;margin-top:1px; }
    .agent-model { color:#c5ccda;font-size:12px;margin-top:10px;overflow-wrap:anywhere; }
    .agent-role { color:var(--muted);font-size:11px;margin-top:5px; }
    .agent-seen { color:#778191;font-size:10px;margin-top:9px;padding-top:8px;border-top:1px solid #292e37; }
    .agent-capabilities { margin-top:10px;padding-top:10px;border-top:1px solid var(--line); }
    .cap-tags { display:flex;gap:5px;flex-wrap:wrap; }
    .cap-tag { display:inline-block;border:1px solid #354052;border-radius:6px;padding:2px 6px;font-size:10px;color:#b7c5db;background:#202936; }
    .cap-tag.passed { color:#a0dfbb;border-color:#355646;background:#1d3028; }
    .cap-tag.failed { color:#f0b2ac;border-color:#62413f;background:#352424; }
    .cap-tag.responded { color:#b6cef3; }
    .cap-caption { margin-top:7px;color:var(--muted);font-size:10px;line-height:1.6;overflow-wrap:anywhere; }
    .workbench-card { margin-top:14px; }
    .workbench-card .avatar { background:#d1c1f0; }
    .cap-button { margin-left:auto;flex:none;padding:5px 9px;border:1px solid #46546b;border-radius:7px;background:#263246;color:#d5e2f7;font-size:11px;cursor:pointer; }
    .cap-button:hover:not(:disabled) { background:#34435c; }
    .cap-button:disabled { opacity:.5;cursor:wait; }
    .cap-result { padding:9px 0;border-top:1px solid var(--line); }
    .cap-result summary { display:flex;align-items:center;justify-content:space-between;gap:6px;cursor:pointer;font-size:11px; }
    .cap-result[open] summary { margin-bottom:7px; }
    .side-note { color:#788292;font-size:11px;line-height:1.6;padding:14px 8px; }
    main { width:100%;height:100vh;height:100dvh;min-width:0;min-height:0;overflow:hidden;display:grid;grid-template-rows:auto auto minmax(0,1fr) auto; }
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
    .workspace-panel { width:100%;margin:0;border:0;border-bottom:1px solid var(--line);background:rgba(26,32,41,.78);padding:9px clamp(18px,4vw,48px); }
    .workspace-heading { display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:5px; }
    .workspace-heading label { color:#d9e1ed;font-size:12px;font-weight:650; }
    .workspace-caption { color:#b8c3d4;font-size:11px; }
    .workspace-row { display:flex;gap:8px; }
    .workspace-path { flex:1;min-width:0;border:1px solid #39414e;border-radius:8px;background:#11151b;color:var(--text);padding:8px 10px;font:12px/1.4 Consolas,"Courier New",monospace; }
    .workspace-path:focus { outline:1px solid #7897c4;border-color:#7897c4; }
    .workspace-apply { flex:none;border:1px solid #43536b;border-radius:8px;background:#263448;color:#dbe8fb;padding:7px 11px;font-size:11px;cursor:pointer; }
    .workspace-apply:hover:not(:disabled) { background:#31445d; }
    .workspace-apply:disabled { opacity:.45;cursor:not-allowed; }
    .workspace-status { color:#8792a2;font-size:10px;margin-top:4px; }
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
    @media (max-width:760px) {
      .app { grid-template-columns:1fr;grid-template-rows:auto minmax(0,1fr); }
      aside { height:auto;max-height:190px;border-right:0;border-bottom:1px solid var(--line);padding:10px 14px;overflow-x:hidden; }
      .brand { padding:0 6px 9px; }
      .brand-sub { display:none; }
      .section-title { margin:3px 4px 7px; }
      #agents { display:flex;gap:8px;overflow-x:auto;padding-bottom:3px; }
      .agent { flex:0 0 min(240px,75vw);padding:9px; }
      .agent-model,.agent-role,.agent-seen,.side-note { display:none; }
      main { height:auto; }
      .topbar { min-height:58px;padding:9px 14px; }
      .topbar .title { font-size:14px; }
      .topbar .subtitle { font-size:10px; }
      .tools { gap:8px; }
      select { max-width:42vw;padding:7px 23px 7px 8px;font-size:11px; }
      .refresh { gap:6px;font-size:10px; }
      .refresh-frequency { display:none; }
      .workspace-panel { padding:8px 14px; }
      .workspace-caption,.workspace-status { display:none; }
      .workspace-heading { margin-bottom:5px; }
      .workspace-row { gap:6px; }
      .workspace-path { padding:7px 8px;font-size:11px; }
      .workspace-apply { padding:7px 8px;font-size:10px; }
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
    <aside>
      <div class="brand"><div class="logo">A</div><div><div class="brand-name">Agent 工作台</div><div class="brand-sub">本机对话 · Codex 与 DeepSeek Harness</div></div></div>
      <div class="section-title"><span>Agents</span><span id="agent-count" class="count">读取中</span></div>
      <div id="agents"></div>
      <article class="agent workbench-card">
        <div class="agent-head"><div class="avatar">W</div><div><div class="agent-name">工作台环境</div><div class="agent-vendor">本机进程 · 实测能力</div></div><button class="cap-button" id="probe-capabilities" type="button">检测</button></div>
        <div class="cap-caption">仅代表工作台进程，不代表上方 Agent 的沙箱权限。</div>
        <div id="capability-results" class="agent-capabilities"></div>
        <div id="capability-error" class="cap-caption" role="status"></div>
      </article>
      <div class="side-note">这里显示已登记到 agent_bridge 的 Agent。最近活动时间反映桥接调用或工作台消息，不代表实时在线状态。</div>
    </aside>
    <main>
      <header class="topbar">
        <div><div class="title">共享对话</div><div class="subtitle">通过 @ 提及选择接收对象</div></div>
        <div class="tools"><select id="conversation" aria-label="选择对话"></select><div class="refresh"><span class="dot"></span><div class="refresh-copy"><span id="status">正在连接</span><span class="refresh-frequency">每 3 秒自动更新</span></div></div></div>
      </header>
      <section class="workspace-panel" aria-label="工作目录设置">
        <div class="workspace-heading"><label for="workspace-path">当前对话的工作目录</label><span class="workspace-caption">用于运行当前对话中的 Agent</span></div>
        <div class="workspace-row"><input class="workspace-path" id="workspace-path" type="text" spellcheck="false" placeholder="C:\Users\you\project" aria-label="工作目录绝对路径"><button class="workspace-apply" id="save-workspace" type="button">应用到此对话</button></div>
        <div class="workspace-status" id="workspace-status">修改后会保留对话消息，并让 Agent 在新目录启动各自的新 CLI 会话。</div>
      </section>
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
const workspacePathInput = document.getElementById("workspace-path");
const saveWorkspaceButton = document.getElementById("save-workspace");
const workspaceStatusNode = document.getElementById("workspace-status");
const workspaceStatusText = "当前对话中的 Agent 会以此目录作为工作目录。修改目录后保留消息记录，并重开 CLI 会话。";
const mentionOptions = [
  {token:"@Codex", label:"Codex", description:"只由 Codex 回复", prefixes:["codex", "openai"]},
  {token:"@DeepSeek Harness", label:"DeepSeek Harness", description:"只由 DeepSeek Harness 回复", prefixes:["deepseek harness", "deepseekharness", "deepseek"]},
  {token:"@all", label:"所有 Agent", description:"同时发送给所有 Agent", prefixes:["all", "everyone", "所有", "全部"]}
];
let selectedConversation = "default";
const agentCards = new Map();
let refreshVersion = 0;
let refreshPending = false;
let refreshPromise = null;
let settingsVersion = 0;
let capabilityVersion = 0;
let initialized = false;
let previousMessageIds = "";
let pendingAgents = new Set();
let availableAgents = {codex:false, "deepseek-harness":false};
let submitting = false;
let stopAllSubmitting = false;
let workspaceSaving = false;
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

async function loadWorkspaceSettings() {
  const conversationId = selectedConversation;
  const version = ++settingsVersion;
  try {
    const settings = await getJson("/api/settings?conversation_id=" + encodeURIComponent(conversationId));
    if (conversationId !== selectedConversation || version !== settingsVersion) return;
    workspacePathInput.value = settings.working_directory;
    workspaceStatusNode.textContent = workspaceStatusText;
  } catch (error) {
    if (conversationId !== selectedConversation || version !== settingsVersion) return;
    workspaceStatusNode.textContent = "读取工作目录设置失败：" + error.message;
  }
}

async function saveWorkspaceSettings() {
  if (workspaceSaving) return;
  const conversationId = selectedConversation;
  const version = ++settingsVersion;
  workspaceSaving = true;
  saveWorkspaceButton.disabled = true;
  workspacePathInput.disabled = true;
  workspaceStatusNode.textContent = "正在验证并应用工作目录…";
  try {
    const response = await fetch("/api/settings", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({conversation_id:conversationId, working_directory:workspacePathInput.value})
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "HTTP " + response.status);
    if (conversationId === selectedConversation && version === settingsVersion) {
      workspacePathInput.value = result.working_directory;
      previousMessageIds = "";
      workspaceStatusNode.textContent = result.changed
        ? "已应用。工作台消息保留，当前对话的 Agent CLI 会话已重置，并将在新目录重新开始。"
        : workspaceStatusText;
    }
    await refreshAll();
  } catch (error) {
    if (conversationId === selectedConversation && version === settingsVersion)
      workspaceStatusNode.textContent = "应用失败：" + error.message;
  } finally {
    workspaceSaving = false;
    await refreshAll();
  }
}

function renderAgents(agents) {
  const ids = new Set(agents.map(function(agent) { return agent.agent_id; }));
  for (const [id, card] of agentCards) {
    if (!ids.has(id)) { card.remove(); agentCards.delete(id); }
  }
  const empty = agentsNode.querySelector(".side-note");
  if (empty) empty.remove();
  document.getElementById("agent-count").textContent = agents.length + " 个已登记";
  for (const agent of agents) {
    let card = agentCards.get(agent.agent_id);
    if (!card) {
    card = textElement("article", "agent");
    const head = textElement("div", "agent-head");
    const avatar = textElement("div", "avatar" + (String(agent.agent_id).includes("deepseek") ? " deepseek" : ""), String(agent.name || "?").slice(0,1));
    const identity = textElement("div");
    identity.append(textElement("div", "agent-name", agent.name || agent.agent_id));
    identity.append(textElement("div", "agent-vendor", agent.vendor || "未知厂商"));
    head.append(avatar, identity);
    card.append(head);
    const capabilitySlot = textElement("div", "agent-capabilities");
    capabilitySlot.dataset.capabilityAgent = agent.agent_id;
    capabilitySlot.append(textElement("span", "cap-tag", "配置读取中"));
    card.append(capabilitySlot);
    card.append(textElement("div", "agent-model", agent.model || "未指定模型"));
    card.append(textElement("div", "agent-role", agent.role || ""));
    card.append(textElement("div", "agent-seen", "最近活动：" + formatTime(agent.last_seen)));
    agentCards.set(agent.agent_id, card);
    }
    const fields = {
      ".agent-name": agent.name || agent.agent_id,
      ".agent-vendor": agent.vendor || "未知厂商",
      ".agent-model": agent.model || "未指定模型",
      ".agent-role": agent.role || "",
      ".agent-seen": "最近活动：" + formatTime(agent.last_seen),
      ".avatar": String(agent.name || "?").slice(0,1)
    };
    for (const [selector, value] of Object.entries(fields)) {
      const node = card.querySelector(selector);
      if (node.textContent !== value) node.textContent = value;
    }
    const index = agents.indexOf(agent);
    if (agentsNode.children[index] !== card) agentsNode.insertBefore(card, agentsNode.children[index] || null);
  }
  if (!agents.length) agentsNode.append(textElement("div", "side-note", "还没有登记 Agent。"));
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

function renderRecipients(agents, codexAvailable, dshAvailable) {
  const registered = new Set(agents.map(function(agent) { return agent.agent_id; }));
  availableAgents = {
    codex: Boolean(codexAvailable),
    "deepseek-harness": Boolean(dshAvailable && registered.has("deepseek-harness"))
  };
}

function recipientsForText(content) {
  const routeText = content.replace(/```[\s\S]*?```|`[^`\r\n]*`/g, function(code) { return " ".repeat(code.length); });
  const codexMentioned = /(^|[^A-Za-z0-9_@\\])@(?:codex|openai)(?![A-Za-z0-9_]|\.[A-Za-z])/i.test(routeText);
  const deepseekMentioned = /(^|[^A-Za-z0-9_@\\])@(?:deepseek\s+harness|deepseekharness|deepseek)(?![A-Za-z0-9_]|\.[A-Za-z])/i.test(routeText);
  const allMentioned = /(^|[^A-Za-z0-9_@\\])@(?:all|everyone|所有|全部)(?![A-Za-z0-9_]|\.[A-Za-z])/i.test(routeText);
  if (allMentioned || (!codexMentioned && !deepseekMentioned)) return ["codex", "deepseek-harness"];
  return [
    ...(codexMentioned ? ["codex"] : []),
    ...(deepseekMentioned ? ["deepseek-harness"] : [])
  ];
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
  return agentId === "deepseek-harness" ? "DeepSeek Harness" : "Codex";
}

function updateComposer() {
  updateMentionSuggestions();
  const recipients = recipientsForText(inputNode.value);
  const busy = recipients.filter(function(agentId) { return pendingAgents.has(agentId); });
  const unavailable = recipients.filter(function(agentId) { return !availableAgents[agentId]; });
  const recipientLabel = recipients.length === 2
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
    const avatar = textElement("div", "avatar" + (String(message.sender_id).includes("deepseek") ? " deepseek" : ""), String(message.sender_name || message.sender_id || "?").slice(0,1));
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
    bubble.append(textElement("div", contentClass, content));
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

async function refreshCapabilities(version = refreshVersion) {
  const conversation = selectedConversation;
  const requestVersion = ++capabilityVersion;
  const data = await getJson("/api/capabilities?conversation_id=" + encodeURIComponent(conversation));
  if (conversation !== selectedConversation || version !== refreshVersion || requestVersion !== capabilityVersion) return;
  document.getElementById("capability-error").textContent = "";
  const root = document.getElementById("capability-results");
  for (const agent of data.agent_configuration) {
    const card = agentCards.get(agent.id);
    const slot = card && card.querySelector("[data-capability-agent]");
    if (!slot) continue;
    const slotSignature = JSON.stringify([agent.cli_found, agent.configuration]);
    if (slot.dataset.signature === slotSignature) continue;
    slot.dataset.signature = slotSignature;
    slot.replaceChildren();
    const tags = textElement("div", "cap-tags");
    tags.append(textElement("span", "cap-tag " + (agent.cli_found ? "passed" : "failed"),
      agent.cli_found ? "CLI 已找到" : "CLI 未找到"));
    tags.append(textElement("span", "cap-tag", "沙箱能力未实测"));
    slot.append(tags, textElement("div", "cap-caption", "启动配置 · " + agent.configuration));
  }
  document.getElementById("probe-capabilities").disabled = data.probe.state === "running";
  document.getElementById("probe-capabilities").textContent = data.probe.state === "running" ? "检测中…" : "检测";
  // Keep expanded diagnostics open across polling when results have not changed.
  const signature = JSON.stringify(data.probe);
  if (root.dataset.signature === signature) return;
  root.dataset.signature = signature;
  root.replaceChildren();
  const stateLabels = {not_tested:"未检测", running:"检测中", completed:"检测完成", failed:"检测失败"};
  root.append(textElement("div", "cap-caption", (stateLabels[data.probe.state] || data.probe.state)
    + (data.probe.expired ? " · 结果已过期" : "")));
  const directory = textElement("div", "cap-caption", data.probe.working_directory);
  directory.title = data.probe.working_directory;
  root.append(directory);
  const labels = {workspace_write:"目录读写", git_read:"Git 本地读取", https:"HTTPS", git_remote_read:"远程仓库读取"};
  const statuses = {passed:"通过", failed:"失败", responded:"收到 HTTP 响应", skipped:"跳过"};
  for (const item of data.probe.results) {
    const row = document.createElement("details");
    row.className = "cap-result";
    const summary = document.createElement("summary");
    const label = item.name === "https" ? new URL(item.target).hostname : (labels[item.name] || item.name);
    summary.append(textElement("span", "", label), textElement("span", "cap-tag " + item.status,
      item.http_status ? "HTTP " + item.http_status : (statuses[item.status] || item.status)));
    row.append(summary);
    row.append(textElement("div", "cap-caption", "目标：" + item.target + "；来源：工作台进程实测；时间：" + formatTime(item.checked_at)
      + "；耗时：" + item.elapsed_ms + "ms；方法：" + item.method + "；说明：" + item.detail));
    root.append(row);
  }
}

document.getElementById("probe-capabilities").addEventListener("click", async function() {
  const conversation = selectedConversation;
  const version = refreshVersion;
  this.disabled = true;
  try {
    const response = await fetch("/api/capabilities/probe", {
      method:"POST", headers:{"Content-Type":"application/json", "X-Capability-Token":capabilityToken},
      body:JSON.stringify({conversation_id:conversation})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "检测请求失败");
    if (conversation === selectedConversation && version === refreshVersion) await refreshCapabilities(version);
  } catch (error) {
    if (conversation === selectedConversation && version === refreshVersion)
      document.getElementById("capability-error").textContent = error.message;
  } finally { this.disabled = false; }
});

async function refreshRound(version, conversation) {
  try {
    const results = await Promise.all([
      getJson("/api/agents"),
      getJson("/api/conversations"),
      getJson("/api/runs")
    ]);
    if (version !== refreshVersion || conversation !== selectedConversation) return;
    renderAgents(results[0].agents);
    renderRecipients(results[0].agents, results[0].codex_cli_available, results[0].dsh_cli_available);
    renderConversations(results[1].conversations);
    if (conversation !== selectedConversation) {
      refreshVersion++;
      refreshPending = true;
      loadWorkspaceSettings();
      return;
    }
    const activeRuns = results[2].runs || [];
    stopAllButton.disabled = stopAllSubmitting || activeRuns.length === 0;
    stopAllButton.textContent = activeRuns.length ? "Stop (" + activeRuns.length + ")" : "Stop";
    const workspaceBusy = activeRuns.some(function(run) { return run.conversation_id === selectedConversation; });
    workspacePathInput.disabled = workspaceBusy;
    saveWorkspaceButton.disabled = workspaceSaving || workspaceBusy;
    if (workspaceBusy) {
      workspaceStatusNode.dataset.busy = "true";
      workspaceStatusNode.textContent = "该对话有 Agent 正在运行，请等待完成或停止后再更改工作目录。";
    } else if (workspaceStatusNode.dataset.busy === "true") {
      delete workspaceStatusNode.dataset.busy;
      workspaceStatusNode.textContent = workspaceStatusText;
    }
    const data = await getJson("/api/messages?conversation_id=" + encodeURIComponent(conversation) + "&limit=500");
    if (version !== refreshVersion || conversation !== selectedConversation) return;
    renderMessages(data.messages);
    try {
      await refreshCapabilities(version);
    } catch (error) {
      if (version === refreshVersion && conversation === selectedConversation)
        document.getElementById("capability-error").textContent = error.message;
    }
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
  loadWorkspaceSettings();
  refreshAll();
});
saveWorkspaceButton.addEventListener("click", saveWorkspaceSettings);
workspacePathInput.addEventListener("keydown", function(event) {
  if (event.key === "Enter") {
    event.preventDefault();
    saveWorkspaceSettings();
  }
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
      body:JSON.stringify({conversation_id:selectedConversation, content:content})
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
loadWorkspaceSettings();
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
    connection = sqlite3.connect(DB_PATH, timeout=10)
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
            # Keep the shared transcript, but start fresh CLI sessions in the new directory.
            connection.execute(
                "DELETE FROM workbench_sessions WHERE conversation_id = ?", (conversation_id,)
            )
            connection.execute(
                "DELETE FROM workbench_agent_sessions WHERE conversation_id = ?", (conversation_id,)
            )
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
    connection = sqlite3.connect(DB_PATH, timeout=10)
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
    connection = sqlite3.connect(DB_PATH, timeout=10)
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
    connection = sqlite3.connect(DB_PATH, timeout=10)
    try:
        with connection:
            connection.execute(
                "UPDATE workbench_messages SET content = ?, status = ?, created_at = ? WHERE id = ?",
                (content, status, utc_now(), message_id),
            )
    finally:
        connection.close()


def update_workbench_message(message_id: int, content: str, status: str) -> None:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    try:
        with connection:
            connection.execute(
                "UPDATE workbench_messages SET content = ?, status = ?, created_at = ? WHERE id = ?",
                (content, status, utc_now(), message_id),
            )
    finally:
        connection.close()


def register_active_run(message_id: int, conversation_id: str, agent_id: str) -> None:
    with ACTIVE_RUNS_LOCK:
        ACTIVE_RUNS[message_id] = {
            "message_id": message_id,
            "conversation_id": conversation_id,
            "agent_id": agent_id,
            "process": None,
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
    with ACTIVE_RUNS_LOCK:
        run = ACTIVE_RUNS.get(message_id)
        if not run:
            return False
        if run["cancel_requested"]:
            return True
        process = run["process"]
        if process is not None and process.poll() is not None:
            return False
        run["cancel_requested"] = True

    try:
        update_workbench_message(message_id, "已停止。", "cancelled")
    finally:
        if process is not None:
            terminate_process_tree(process)
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
            # stop_active_run still terminates the process in its finally block.
            stopped += 1
    return stopped


def run_managed_process(message_id: int, command: list[str], **kwargs) -> subprocess.CompletedProcess:
    timeout = kwargs.pop("timeout", None)
    input_data = kwargs.pop("input", None)
    kwargs["stdout"] = subprocess.PIPE
    kwargs["stderr"] = subprocess.PIPE
    if input_data is not None:
        kwargs["stdin"] = subprocess.PIPE
    if os.name == "nt":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        kwargs["start_new_session"] = True

    with ACTIVE_RUNS_LOCK:
        run = ACTIVE_RUNS.get(message_id)
        if not run or run["cancel_requested"]:
            raise AgentRunCancelled()
        process = subprocess.Popen(command, **kwargs)
        run["process"] = process

    try:
        stdout, stderr = process.communicate(input=input_data, timeout=timeout)
    except subprocess.TimeoutExpired as timeout_error:
        terminate_process_tree(process)
        stdout, stderr = process.communicate()
        if is_run_cancelled(message_id):
            raise AgentRunCancelled() from timeout_error
        raise subprocess.TimeoutExpired(
            command,
            timeout,
            output=stdout if stdout is not None else timeout_error.output,
            stderr=stderr if stderr is not None else timeout_error.stderr,
        ) from timeout_error

    if is_run_cancelled(message_id):
        raise AgentRunCancelled()
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def touch_agent_last_seen(agent_id: str) -> None:
    connection = sqlite3.connect(DB_PATH, timeout=10)
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
    return workspace_context + shared_context + "当前用户消息：\n" + prompt


def run_codex(
    conversation_id: str,
    prompt: str,
    assistant_message_id: int,
    user_message_id: int,
) -> None:
    try:
        touch_agent_last_seen("codex")
        working_directory = resolve_working_directory(conversation_id)
        codex_command = resolve_codex_command()
        if not codex_command:
            raise RuntimeError(
                "找不到 Codex CLI。请确认已安装 Codex；也可以设置 CODEX_CLI 为 codex.exe 的完整路径。"
            )

        thread_id = get_codex_thread_id(conversation_id)
        agent_prompt = build_agent_prompt(
            conversation_id, "codex", user_message_id, prompt, working_directory
        )
        with tempfile.TemporaryDirectory(prefix="agent-workbench-") as temporary_directory:
            answer_path = Path(temporary_directory) / "codex-answer.txt"
            if thread_id:
                command = [
                    codex_command, "exec", "--sandbox", CODEX_SANDBOX,
                    "--cd", str(working_directory), "resume",
                    "--json", "--skip-git-repo-check", "--output-last-message", str(answer_path),
                    thread_id, agent_prompt,
                ]
            else:
                command = [
                    codex_command, "exec", "--sandbox", CODEX_SANDBOX,
                    "--json", "--skip-git-repo-check",
                    "--cd", str(working_directory),
                    "--output-last-message", str(answer_path), agent_prompt,
                ]

            result = run_managed_process(
                assistant_message_id,
                command,
                cwd=working_directory,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
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
                save_codex_thread_id(conversation_id, thread_id)

            answer = answer_path.read_text(encoding="utf-8").strip() if answer_path.exists() else ""
            if not answer:
                raise RuntimeError("Codex 没有返回可显示的最终回复。")
            update_codex_message(assistant_message_id, answer, "completed")
    except AgentRunCancelled:
        update_codex_message(assistant_message_id, "已停止。", "cancelled")
    except subprocess.TimeoutExpired:
        if is_run_cancelled(assistant_message_id):
            update_codex_message(assistant_message_id, "已停止。", "cancelled")
        else:
            update_codex_message(assistant_message_id, "Codex 执行超时（5 分钟）。", "failed")
    except Exception as error:
        if is_run_cancelled(assistant_message_id):
            update_codex_message(assistant_message_id, "已停止。", "cancelled")
        else:
            message = str(error).strip() or error.__class__.__name__
            update_codex_message(assistant_message_id, "调用 Codex 失败：" + message[:4000], "failed")


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


def run_deepseek_harness(
    conversation_id: str,
    prompt: str,
    assistant_message_id: int,
    user_message_id: int,
) -> None:
    try:
        touch_agent_last_seen("deepseek-harness")
        working_directory = resolve_working_directory(conversation_id)
        dsh_command = resolve_dsh_command()
        if not dsh_command:
            raise RuntimeError(
                "找不到 dsh CLI。请在 DeepSeek Harness 桌面端的“Manage dsh Command”中安装命令，"
                "重新打开 PowerShell 并重启工作台；也可设置 DSH_CLI 为 dsh 可执行文件的完整路径。"
            )
        if not DSH_AUTH_PATCH.is_file():
            raise RuntimeError(f"找不到 DSH headless 登录配置：{DSH_AUTH_PATCH}")

        # Use the configured headless profile. Keep a separate session key so
        # workbench sessions created in earlier isolated profiles are not resumed here.
        session_id = get_agent_session_id(conversation_id, DSH_SESSION_KEY)
        if session_id and not all(char.isalnum() or char in "._-" for char in session_id):
            raise RuntimeError("保存的 DeepSeek Harness session_id 格式无效，请新建工作台对话后重试。")

        agent_prompt = build_agent_prompt(
            conversation_id, "deepseek-harness", user_message_id, prompt, working_directory
        )
        arguments = [
            "--profile", DSH_PROFILE,
            "--patch", str(DSH_AUTH_PATCH),
            "--json",
        ]
        if session_id:
            arguments.extend(["--session-id", session_id])
        arguments.append("-")
        command, use_shell, use_electron_as_node = build_dsh_command(dsh_command, arguments)
        environment = os.environ.copy()
        if use_electron_as_node:
            environment["ELECTRON_RUN_AS_NODE"] = "1"
        result = run_managed_process(
            assistant_message_id,
            command,
            cwd=working_directory,
            env=environment,
            input=agent_prompt,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
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
        update_workbench_message(assistant_message_id, answer, "completed")
    except AgentRunCancelled:
        update_workbench_message(assistant_message_id, "已停止。", "cancelled")
    except subprocess.TimeoutExpired:
        if is_run_cancelled(assistant_message_id):
            update_workbench_message(assistant_message_id, "已停止。", "cancelled")
        else:
            update_workbench_message(assistant_message_id, "DeepSeek Harness 执行超时（10 分钟）。", "failed")
    except Exception as error:
        if is_run_cancelled(assistant_message_id):
            update_workbench_message(assistant_message_id, "已停止。", "cancelled")
        else:
            message = str(error).strip() or error.__class__.__name__
            update_workbench_message(assistant_message_id, "调用 DeepSeek Harness 失败：" + message[:4000], "failed")


def run_agent_task(
    agent_id: str,
    conversation_id: str,
    prompt: str,
    assistant_message_id: int,
    user_message_id: int,
) -> None:
    try:
        worker = run_codex if agent_id == "codex" else run_deepseek_harness
        worker(conversation_id, prompt, assistant_message_id, user_message_id)
    finally:
        unregister_active_run(assistant_message_id)


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
                {"id": "codex", "name": "Codex", "configuration": f"--sandbox {CODEX_SANDBOX}",
                 "cli_found": available["codex_cli_available"]},
                {"id": "deepseek-harness", "name": "DeepSeek Harness", "configuration": f"--profile {DSH_PROFILE}; 未显式传入 sandbox 参数，其他策略未知",
                 "cli_found": available["dsh_cli_available"]},
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
        if not recipients or not agent_prompts:
            self.send_json(400, {"error": "请在 Agent 名称后输入要发送的消息"})
            return

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

        connection = sqlite3.connect(DB_PATH, timeout=10)
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
                label = "Codex" if pending[0] == "codex" else "DeepSeek Harness"
                self.send_json(409, {
                    "error": label + " 正在处理这段对话。请等待它完成，或只 @ 另一个 Agent。"
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
            if active_run:
                connection.rollback()
                label = "Codex" if active_run["agent_id"] == "codex" else "DeepSeek Harness"
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


def main() -> None:
    parser = argparse.ArgumentParser(description="启动本地 Agent 工作台并接入 Codex CLI 与 DeepSeek Harness")
    parser.add_argument("--port", type=int, default=8765, help="本地网页端口（默认：8765）")
    args = parser.parse_args()
    initialize_workbench_tables()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), ChatHandler)
    print(f"Agent 工作台已启动：http://127.0.0.1:{args.port}")
    print(f"共享数据库：{DB_PATH}")
    print("按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭 Agent 工作台……")
    finally:
        try:
            stop_all_active_runs()
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
