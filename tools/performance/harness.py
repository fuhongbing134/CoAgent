"""Isolated process-local instrumentation. Importing this module starts nothing."""
import dataclasses
import hashlib
from inspect import getattr_static
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time

from core import Budget


class Harness:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        self.db = self.root / "experiment.db"
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.budget = Budget()
        self.events = []
        self.lock = threading.Lock()
        self.modes = {}
        self.executions = []
        self.originals = []

    def event(self, name, **data):
        with self.lock:
            self.events.append(dict(event=name, ns=time.perf_counter_ns(), **data))

    def replace(self, obj, name, replacement):
        self.originals.append((obj, name, getattr_static(obj, name)))
        setattr(obj, name, replacement)

    def prepare(self):
        if "web_chat" in sys.modules or "agent_bridge" in sys.modules or self.db.exists():
            raise RuntimeError("fresh process and absent database required")
        # The runner is itself disposable: do this BEFORE importing product modules.
        allowed = {k: os.environ[k] for k in ("SystemRoot", "WINDIR", "COMSPEC", "PATH", "PATHEXT")
                   if k in os.environ}
        allowed.update(TEMP=str(self.root), TMP=str(self.root), HOME=str(self.root),
                       USERPROFILE=str(self.root), APPDATA=str(self.root), LOCALAPPDATA=str(self.root),
                       AGENT_BRIDGE_DB=str(self.db), DSH_HEADLESS_PATCH=str(self.root / "patch.yml"),
                       PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
                       AGENT_CODEX_TIMEOUT_SECONDS="5", AGENT_DEEPSEEK_TIMEOUT_SECONDS="5",
                       AGENT_GROK_TIMEOUT_SECONDS="5")
        os.environ.clear()
        os.environ.update(allowed)
        (self.root / "patch.yml").write_text("{}", encoding="utf-8")
        (self.workspace / "AGENTS.md").write_text("Controlled fixture workspace. No network or tools.", encoding="utf-8")
        (self.workspace / "specs").mkdir()
        (self.workspace / "specs" / "README.md").write_text("Fixture only.", encoding="utf-8")
        import web_chat as web
        self.web = web
        if web.DB_PATH.resolve() != self.db or web.SESSION_CONFIGURATION_RESOLVER is not None:
            raise RuntimeError("isolation or default session configuration mismatch")
        python = str(Path(sys.executable).resolve(strict=True))
        for name in ("resolve_codex_command", "resolve_dsh_command", "resolve_grok_command"):
            self.replace(web, name, lambda: python)
            self.replace(web.ChatHandler, name, staticmethod(lambda: python))
        from workbench.adapters import deepseek_command
        self.replace(deepseek_command, "resolve_dsh_desktop_runtime", lambda command: None)
        fixture_directory = lambda conversation: self.workspace
        self.replace(web, "resolve_working_directory", fixture_directory)
        self.replace(web.ChatHandler, "resolve_working_directory", staticmethod(fixture_directory))
        if web.build_dsh_command(python, ["--json", "-"])[1:] != (False, False):
            raise RuntimeError("DSH shell/shim not disabled")
        original_begin = web.begin_execution
        def begin(message, **kwargs):
            self.event("admission_requested", message=message)
            execution = original_begin(message, **kwargs)
            self.executions.append(execution)
            token = execution.token
            token_data = dataclasses.asdict(token) if dataclasses.is_dataclass(token) else dict(run_id=str(token))
            self.event("admitted", message=message, token=token_data)
            return execution
        self.replace(web, "begin_execution", begin)
        original_finish = web.finish_execution
        def finish(message, **kwargs):
            value = original_finish(message, **kwargs)
            self.event("finish_returned", message=message, released=value)
            return value
        self.replace(web, "finish_execution", finish)
        original_invoke = web.invoke_collaboration
        def invoke(job, agent, role, prompt, **kwargs):
            self.event("invoke_entered", job=job, agent=agent, role=role, call=kwargs["call_id"])
            return original_invoke(job, agent, role, prompt, **kwargs)
        self.replace(web, "invoke_collaboration", invoke)
        original_run = web.run_managed_process
        def run(message, command, **kwargs):
            active = web.active_run(message)
            execution = active["execution"]
            agent, conversation = active["agent_id"], active["conversation_id"]
            delay, fault = self.modes.get(conversation, (.75, "none"))
            role, job, call = "direct", None, None
            if active.get("collaboration"):
                job, call = active["collaboration"]
                with sqlite3.connect(self.db) as db:
                    role = db.execute("SELECT role FROM collab_calls WHERE job_id=? AND call_id=?", (job, call)).fetchone()[0]
            elif getattr(web.COLLAB_CONTEXT, "validating_entry", False):
                role, job, call = "entry", "entry-"+str(message), "entry"
            sid = execution.selected_session_id or "fixture-"+hashlib.sha256((agent+conversation).encode()).hexdigest()[:20]
            if kwargs.get("shell", False) or Path(command[0]).resolve() != Path(python):
                raise RuntimeError("unexpected real executable or shell")
            if Path(kwargs["cwd"]).resolve() != self.workspace:
                raise RuntimeError("working directory escaped fixture")
            argv = [python, str(Path(__file__).with_name("fixture.py").resolve()),
                    "--adapter", agent, "--sid", sid, "--role", role,
                    "--marker", "fixture-message-"+str(message), "--delay", str(delay),
                    "--fault", fault, *command[1:]]
            self.budget.start(job)
            self.event("launch_requested", message=message, job=job, call=call, role=role,
                       agent=agent, session=sid, requested=command, actual=argv)
            old_stage = kwargs.get("on_stage")
            def stage(name, process):
                if old_stage:
                    old_stage(name, process)
                self.event("process_"+name, message=message, pid=getattr(getattr(process, "info", None), "dwProcessId", None))
            kwargs["on_stage"] = stage
            kwargs["capture_limit"] = 4096
            # Worker receives an environment copy, itself already scrubbed above.
            kwargs["env"] = os.environ.copy()
            if fault == "timeout":
                kwargs["timeout"] = .2
            try:
                return original_run(message, argv, **kwargs)
            finally:
                result = execution.result
                self.event("result_observed", message=message,
                           outcome=getattr(result, "outcome", "missing"),
                           exit_evidence=getattr(result, "exit_evidence", "missing"))
                self.budget.settle(getattr(result, "exit_evidence", "unknown"))
        self.replace(web, "run_managed_process", run)
        from workbench.collaboration.collaboration_actions import ActionStore
        original_accept = ActionStore.accept_result
        def accept(store, job, call, *args, **kwargs):
            receipt = original_accept(store, job, call, *args, **kwargs)
            self.event("protocol_receipt_returned", job=job, call=call,
                       disposition=getattr(receipt, "disposition", "unknown"))
            return receipt
        self.replace(ActionStore, "accept_result", accept)
        # The actual lifecycle counts calls after entry; permit only the fixed plan.
        from workbench.collaboration.collaboration_service import CollaborationService
        original_register = CollaborationService.register_entry
        def register(service, *args, **kwargs):
            value = original_register(service, *args, **kwargs)
            with service.store.transaction() as db:
                db.execute("UPDATE collab_lifecycle SET round_limit=5 WHERE job_id=?", (args[0],))
            return value
        self.replace(CollaborationService, "register_entry", register)
        return web

    def cleanup(self):
        self.budget.stopped.set()
        web = self.web
        with web.ACTIVE_RUNS_LOCK:
            ids = list(web.ACTIVE_RUNS)
        for message in ids:
            web.stop_active_run(message)
        deadline = time.monotonic()+15
        for execution in self.executions:
            execution.done.wait(max(0, deadline-time.monotonic()))
        unfinished = [e for e in self.executions if not e.done.is_set() or not e.released]
        self.event("cleanup", unresolved=len(unfinished))
        return not unfinished
