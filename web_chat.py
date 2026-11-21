"""Local multi-agent workbench backed by the agent_bridge SQLite database."""

import argparse
import hashlib
import uuid
import json
import logging
import os
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
from http.server import ThreadingHTTPServer
from pathlib import Path


from workbench.storage import web_store
from workbench.execution import web_runs
from workbench.adapters import web_backends
from workbench.workers import dispatch
from workbench.web.routes import ChatHandler as WorkbenchRoutes
from workbench.workers.result import WorkerResult, structured_worker
from workbench.web.page import PAGE
from workbench.prompts.routing import clean_mention_segment, clean_message_prompt, resolve_message_recipients
from workbench.prompts.context import build_agent_prompt as assemble_agent_prompt, truncate_head_tail
from workbench.adapters.deepseek_command import resolve_dsh_desktop_runtime, build_dsh_command
from workbench.agents.agent_registry import default_registry
from workbench.adapters.grok_adapter import command as grok_command_args, parse_output as grok_parse_output, diagnostic as grok_diagnostic
from workbench.storage.database_schema import ensure_schema, require_current
from workbench.execution.migration_barrier import MigrationBarrier
from workbench.execution.service_instance import ServiceInstanceLock
from workbench.agents.capability_probe import ProbeManager
from workbench.collaboration.collaboration_entry import route_message, entry_prompt, parse_entry_response
from workbench.collaboration.collaboration_batches import BatchStore
from workbench.collaboration.collaboration_lifecycle import LifecycleStore
from workbench.collaboration.collaboration_actions import ActionStore
from workbench.collaboration.collaboration_service import CollaborationService, InvocationResult
from workbench.execution.execution_runtime import Execution, ExecutionAdmissionError, format_execution_error
from workbench.execution.execution_inspection import InspectionQueryError, read_occupancy_snapshot
from workbench.execution.execution_tracking import DomainBusy, invalidate_sessions


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
AGENT_CONTEXT_MAX_MESSAGE_CHARS = _env_positive_int("AGENT_CONTEXT_MAX_MESSAGE_CHARS", 3000)
AGENT_CODEX_TIMEOUT_SECONDS = _env_positive_int("AGENT_CODEX_TIMEOUT_SECONDS", 1200)
AGENT_DEEPSEEK_TIMEOUT_SECONDS = _env_positive_int("AGENT_DEEPSEEK_TIMEOUT_SECONDS", 1200)
AGENT_GROK_TIMEOUT_SECONDS = _env_positive_int("AGENT_GROK_TIMEOUT_SECONDS", 1200)
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
                                                  finalize_message=finalize_message)
        return COLLAB_SERVICE


def cancel_collaboration(job_id):
    return dispatch.cancel_collaboration(
        job_id,
        ACTIVE_RUNS=ACTIVE_RUNS,
        ACTIVE_RUNS_LOCK=ACTIVE_RUNS_LOCK,
        COLLAB_MESSAGES=COLLAB_MESSAGES,
        connect_workbench=connect_workbench,
        stop_active_run=stop_active_run,
    )


def invoke_collaboration(job_id, agent_id, role, prompt, *, call_id, entry_mode=False):
    return dispatch.invoke_collaboration(
        job_id,
        agent_id,
        role,
        prompt,
        call_id=call_id,
        entry_mode=entry_mode,
        ACTIVE_RUNS_LOCK=ACTIVE_RUNS_LOCK,
        COLLAB_AGENT_LOCKS=COLLAB_AGENT_LOCKS,
        COLLAB_CONTEXT=COLLAB_CONTEXT,
        COLLAB_MESSAGES=COLLAB_MESSAGES,
        active_run=active_run,
        agent_worker=agent_worker,
        begin_execution=begin_execution,
        collaboration_service=collaboration_service,
        connect_workbench=connect_workbench,
        finish_execution=finish_execution,
        register_active_run=register_active_run,
        unregister_active_run=unregister_active_run,
        update_workbench_message=update_workbench_message,
        utc_now=utc_now,
    )


COLLAB_CONTEXT = threading.local()


AgentRunCancelled = web_runs.AgentRunCancelled


utc_now = web_store.utc_now


_WorkbenchConnection = web_store._WorkbenchConnection


def connect_workbench(timeout=10):
    return web_store.connect_workbench(
        timeout,
        DB_PATH=DB_PATH,
    )


def initialize_workbench_tables() -> None:
    return web_store.initialize_workbench_tables(
        DB_PATH=DB_PATH,
        connect_workbench=connect_workbench,
    )


def open_database() -> sqlite3.Connection:
    return web_store.open_database(
        DB_PATH=DB_PATH,
    )


def get_working_directory_text(conversation_id: str) -> str:
    return web_store.get_working_directory_text(
        conversation_id,
        PROJECT_DIR=PROJECT_DIR,
        open_database=open_database,
    )


def resolve_working_directory(conversation_id: str) -> Path:
    return web_store.resolve_working_directory(
        conversation_id,
        get_working_directory_text=get_working_directory_text,
    )


normalize_working_directory = web_store.normalize_working_directory


def save_working_directory(conversation_id: str, working_directory: str) -> bool:
    return web_store.save_working_directory(
        conversation_id,
        working_directory,
        PROJECT_DIR=PROJECT_DIR,
        SESSION_CONFIGURATION_RESOLVER=SESSION_CONFIGURATION_RESOLVER,
        connect_workbench=connect_workbench,
    )


def fetch_agents() -> list[dict]:
    return web_store.fetch_agents(
        open_database=open_database,
    )


def fetch_conversations() -> list[dict]:
    return web_store.fetch_conversations(
        open_database=open_database,
    )


def fetch_messages(conversation_id: str, limit: int) -> list[dict]:
    return web_store.fetch_messages(
        conversation_id,
        limit,
        open_database=open_database,
    )


def get_codex_thread_id(conversation_id: str) -> str | None:
    return web_store.get_codex_thread_id(
        conversation_id,
        open_database=open_database,
    )


def save_codex_thread_id(conversation_id: str, thread_id: str) -> None:
    return web_store.save_codex_thread_id(
        conversation_id,
        thread_id,
        SESSION_CONFIGURATION_RESOLVER=SESSION_CONFIGURATION_RESOLVER,
        connect_workbench=connect_workbench,
    )


def get_agent_session_id(conversation_id: str, agent_id: str) -> str | None:
    return web_store.get_agent_session_id(
        conversation_id,
        agent_id,
        open_database=open_database,
    )


def save_agent_session_id(conversation_id: str, agent_id: str, session_id: str) -> None:
    return web_store.save_agent_session_id(
        conversation_id,
        agent_id,
        session_id,
        SESSION_CONFIGURATION_RESOLVER=SESSION_CONFIGURATION_RESOLVER,
        connect_workbench=connect_workbench,
    )


resolve_dsh_command = web_backends.resolve_dsh_command


resolve_grok_command = web_backends.resolve_grok_command


resolve_codex_command = web_backends.resolve_codex_command


def update_codex_message(message_id: int, content: str, status: str) -> None:
    update_workbench_message(message_id, content, status)


def update_workbench_message(message_id: int, content: str, status: str) -> None:
    return web_store.update_workbench_message(
        message_id,
        content,
        status,
        COLLAB_CONTEXT=COLLAB_CONTEXT,
        connect_workbench=connect_workbench,
    )


def stage_collaboration_reply(message_id, job_id, call_id, text):
    return web_store.stage_collaboration_reply(
        message_id,
        job_id,
        call_id,
        text,
        connect_workbench=connect_workbench,
    )


finalize_message = web_store.finalize_message


def worker_reply(message_id, text, status):
    return dispatch.worker_reply(
        message_id,
        text,
        status,
        active_run=active_run,
        stage_collaboration_reply=stage_collaboration_reply,
        update_workbench_message=update_workbench_message,
    )


def active_run(message_id):
    return web_runs.active_run(
        message_id,
        ACTIVE_RUNS=ACTIVE_RUNS,
        ACTIVE_RUNS_LOCK=ACTIVE_RUNS_LOCK,
    )


def begin_execution(message_id, *, source_kind, job_id=None, call_id=None):
    return web_runs.begin_execution(
        message_id,
        source_kind=source_kind,
        job_id=job_id,
        call_id=call_id,
        DB_PATH=DB_PATH,
        SERVICE_INSTANCE_ID=SERVICE_INSTANCE_ID,
        active_run=active_run,
        resolve_working_directory=resolve_working_directory,
    )


def finish_execution(message_id, *, outcome=None):
    return web_runs.finish_execution(
        message_id,
        outcome=outcome,
        active_run=active_run,
        connect_workbench=connect_workbench,
    )


def register_active_run(message_id: int, conversation_id: str, agent_id: str) -> None:
    return web_runs.register_active_run(
        message_id,
        conversation_id,
        agent_id,
        ACTIVE_RUNS=ACTIVE_RUNS,
        ACTIVE_RUNS_LOCK=ACTIVE_RUNS_LOCK,
        utc_now=utc_now,
    )


def unregister_active_run(message_id: int) -> None:
    return web_runs.unregister_active_run(
        message_id,
        ACTIVE_RUNS=ACTIVE_RUNS,
        ACTIVE_RUNS_LOCK=ACTIVE_RUNS_LOCK,
    )


def is_run_cancelled(message_id: int) -> bool:
    return web_runs.is_run_cancelled(
        message_id,
        ACTIVE_RUNS=ACTIVE_RUNS,
        ACTIVE_RUNS_LOCK=ACTIVE_RUNS_LOCK,
    )


def list_active_runs() -> list[dict]:
    return web_runs.list_active_runs(
        ACTIVE_RUNS=ACTIVE_RUNS,
        ACTIVE_RUNS_LOCK=ACTIVE_RUNS_LOCK,
    )


terminate_process_tree = web_runs.terminate_process_tree


def stop_active_run(message_id: int) -> bool:
    return web_runs.stop_active_run(
        message_id,
        active_run=active_run,
        update_workbench_message=update_workbench_message,
    )


def stop_all_active_runs() -> int:
    return web_runs.stop_all_active_runs(
        ACTIVE_RUNS=ACTIVE_RUNS,
        ACTIVE_RUNS_LOCK=ACTIVE_RUNS_LOCK,
        stop_active_run=stop_active_run,
    )


def prepare_execution_session(message_id, agent_id, executable, environment):
    return web_runs.prepare_execution_session(
        message_id,
        agent_id,
        executable,
        environment,
        DSH_SESSION_KEY=DSH_SESSION_KEY,
        GROK_SESSION_KEY=GROK_SESSION_KEY,
        SESSION_CONFIGURATION_RESOLVER=SESSION_CONFIGURATION_RESOLVER,
        active_run=active_run,
    )


def run_managed_process(message_id: int, command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return web_runs.run_managed_process(
        message_id,
        command,
        active_run=active_run,
        **kwargs,
    )


def touch_agent_last_seen(agent_id: str) -> None:
    return web_store.touch_agent_last_seen(
        agent_id,
        connect_workbench=connect_workbench,
    )


def build_agent_prompt(conversation_id: str, agent_id: str, user_message_id: int,
                       prompt: str, working_directory: Path) -> str:
    return assemble_agent_prompt(
        conversation_id, agent_id, user_message_id, prompt, working_directory,
        open_database=open_database, max_messages=AGENT_CONTEXT_MAX_MESSAGES,
        max_message_chars=AGENT_CONTEXT_MAX_MESSAGE_CHARS,
    )


@structured_worker
def run_codex(
    conversation_id: str,
    prompt: str,
    assistant_message_id: int,
    user_message_id: int,
) -> WorkerResult:
    return web_backends.run_codex(
        conversation_id,
        prompt,
        assistant_message_id,
        user_message_id,
        AGENT_CODEX_TIMEOUT_SECONDS=AGENT_CODEX_TIMEOUT_SECONDS,
        CODEX_SANDBOX=CODEX_SANDBOX,
        COLLAB_CONTEXT=COLLAB_CONTEXT,
        active_run=active_run,
        build_agent_prompt=build_agent_prompt,
        is_run_cancelled=is_run_cancelled,
        prepare_execution_session=prepare_execution_session,
        resolve_codex_command=resolve_codex_command,
        run_managed_process=run_managed_process,
        save_codex_thread_id=save_codex_thread_id,
        touch_agent_last_seen=touch_agent_last_seen,
        worker_reply=worker_reply,
    )


@structured_worker
def run_deepseek_harness(
    conversation_id: str,
    prompt: str,
    assistant_message_id: int,
    user_message_id: int,
) -> WorkerResult:
    return web_backends.run_deepseek_harness(
        conversation_id,
        prompt,
        assistant_message_id,
        user_message_id,
        AGENT_DEEPSEEK_TIMEOUT_SECONDS=AGENT_DEEPSEEK_TIMEOUT_SECONDS,
        DSH_AUTH_PATCH=DSH_AUTH_PATCH,
        DSH_PROFILE=DSH_PROFILE,
        DSH_SESSION_KEY=DSH_SESSION_KEY,
        active_run=active_run,
        build_agent_prompt=build_agent_prompt,
        is_run_cancelled=is_run_cancelled,
        prepare_execution_session=prepare_execution_session,
        resolve_dsh_command=resolve_dsh_command,
        run_managed_process=run_managed_process,
        save_agent_session_id=save_agent_session_id,
        touch_agent_last_seen=touch_agent_last_seen,
        worker_reply=worker_reply,
    )


build_grok_command = web_backends.build_grok_command


parse_grok_output = web_backends.parse_grok_output


@structured_worker
def run_grok(
    conversation_id: str,
    prompt: str,
    assistant_message_id: int,
    user_message_id: int,
) -> WorkerResult:
    return web_backends.run_grok(
        conversation_id,
        prompt,
        assistant_message_id,
        user_message_id,
        AGENT_GROK_TIMEOUT_SECONDS=AGENT_GROK_TIMEOUT_SECONDS,
        COLLAB_CONTEXT=COLLAB_CONTEXT,
        GROK_SESSION_KEY=GROK_SESSION_KEY,
        active_run=active_run,
        build_agent_prompt=build_agent_prompt,
        build_grok_command=build_grok_command,
        is_run_cancelled=is_run_cancelled,
        parse_grok_output=parse_grok_output,
        prepare_execution_session=prepare_execution_session,
        resolve_grok_command=resolve_grok_command,
        run_managed_process=run_managed_process,
        save_agent_session_id=save_agent_session_id,
        touch_agent_last_seen=touch_agent_last_seen,
        worker_reply=worker_reply,
    )


def agent_worker(agent_id: str):
    return dispatch.agent_worker(
        agent_id,
        run_codex=run_codex,
        run_deepseek_harness=run_deepseek_harness,
        run_grok=run_grok,
    )


def run_agent_task(
    agent_id: str,
    conversation_id: str,
    prompt: str,
    assistant_message_id: int,
    user_message_id: int,
) -> None:
    return dispatch.run_agent_task(
        agent_id,
        conversation_id,
        prompt,
        assistant_message_id,
        user_message_id,
        COLLAB_CONTEXT=COLLAB_CONTEXT,
        active_run=active_run,
        agent_worker=agent_worker,
        begin_execution=begin_execution,
        collaboration_service=collaboration_service,
        connect_workbench=connect_workbench,
        fail_entry_message=fail_entry_message,
        finish_execution=finish_execution,
        is_run_cancelled=is_run_cancelled,
        unregister_active_run=unregister_active_run,
    )


def fail_entry_message(message_id, stage, error):
    return dispatch.fail_entry_message(
        message_id,
        stage,
        error,
        active_run=active_run,
        connect_workbench=connect_workbench,
    )


class ChatHandler(WorkbenchRoutes):
    """Bind the HTTP routes to the local workbench services."""

    ACTIVE_RUNS = ACTIVE_RUNS
    ACTIVE_RUNS_LOCK = ACTIVE_RUNS_LOCK
    AGENT_LABELS = AGENT_LABELS
    CAPABILITY_PROBES = CAPABILITY_PROBES
    CAPABILITY_TOKEN = CAPABILITY_TOKEN
    CODEX_SANDBOX = CODEX_SANDBOX
    COLLAB_MESSAGES = COLLAB_MESSAGES
    DSH_PROFILE = DSH_PROFILE
    MAX_MESSAGES = MAX_MESSAGES
    MAX_MESSAGE_LENGTH = MAX_MESSAGE_LENGTH
    cli_availability = staticmethod(cli_availability)
    collaboration_service = staticmethod(collaboration_service)
    connect_workbench = staticmethod(connect_workbench)
    fetch_agents = staticmethod(fetch_agents)
    fetch_conversations = staticmethod(fetch_conversations)
    fetch_messages = staticmethod(fetch_messages)
    get_working_directory_text = staticmethod(get_working_directory_text)
    list_active_runs = staticmethod(list_active_runs)
    normalize_working_directory = staticmethod(normalize_working_directory)
    register_active_run = staticmethod(register_active_run)
    resolve_codex_command = staticmethod(resolve_codex_command)
    resolve_dsh_command = staticmethod(resolve_dsh_command)
    resolve_grok_command = staticmethod(resolve_grok_command)
    resolve_message_recipients = staticmethod(resolve_message_recipients)
    resolve_working_directory = staticmethod(resolve_working_directory)
    run_agent_task = staticmethod(run_agent_task)
    save_working_directory = staticmethod(save_working_directory)
    stop_active_run = staticmethod(stop_active_run)
    stop_all_active_runs = staticmethod(stop_all_active_runs)
    utc_now = staticmethod(utc_now)


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
            agent_bridge.initialize_db(allow_legacy=allow_legacy, db_path=DB_PATH)
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
