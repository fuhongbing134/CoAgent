"""Local, allowlisted evidence for a single ordinary Grok run."""

import hashlib
import json
import logging
import re
import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from workbench.execution.execution_tracking import StaleRun

SCHEMA_VERSION = 1
TOOLS = ("create_file", "edit_file", "delete_file")
SERVER_EVENTS = {event.encode("ascii"): event for event in
                 ("started", "initialize_response", "tools_list_response")}


def read_evidence(path, consume):
    try:
        with path.open("rb") as stream:
            for line in stream.read(1024 * 1024).splitlines():
                consume(line)
    except OSError:
        pass


def client_evidence(line, summary):
    try:
        event = json.loads(line)
    except (ValueError, RecursionError):
        return
    if not isinstance(event, dict):
        return
    kind = event.get("event")
    if kind == "version":
        version = event.get("version")
        if isinstance(version, str) and re.fullmatch(r"\d{1,4}\.\d{1,4}\.\d{1,4}(?: \([a-f0-9]{7,40}\))?", version):
            summary["grok_version"] = version
    if event.get("server") != "workbench_files":
        return
    if kind in ("definition_loaded", "server_started", "initialize", "tools_list", "tools_registered"):
        status = event.get("status")
        if status in ("ok", "error"):
            summary["client_stages"][kind] = status
            code = event.get("code")
            if type(code) is int and -65536 <= code <= 65535:
                summary.setdefault("client_error_codes", {})[kind] = code
        names = event.get("tools")
        if kind == "tools_registered" and status == "ok" and isinstance(names, list):
            summary["client_tools"] = [
                name for tool in TOOLS if (name := f"workbench_files__{tool}") in names
            ]


def record_diagnostic(execution, debug_path, server_path, command):
    arguments = command or []
    summary = {
        "workbench_diagnostic_revision": SCHEMA_VERSION,
        "arguments": {
            "agent": "--agent" in arguments,
            "mcp_allow": "MCPTool(workbench_files__*)" in arguments,
            "debug_file": "--debug-file" in arguments,
        },
        "client_stages": {},
        "server_events": [],
    }
    result = execution.result
    if result is not None:
        summary["process_outcome"] = result.outcome
        summary["exit_code"] = result.returncode
    if arguments:
        executable = shutil.which(arguments[0])
        if executable:
            try:
                with Path(executable).open("rb") as stream:
                    summary["grok_executable_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
            except OSError:
                pass

    server_events = set()
    def remember_server_event(line):
        if line in SERVER_EVENTS:
            server_events.add(SERVER_EVENTS[line])

    read_evidence(server_path, remember_server_event)
    summary["server_events"] = sorted(server_events)
    if "tools_list_response" in server_events:
        summary["server_tools"] = list(TOOLS)
    read_evidence(debug_path, lambda line: client_evidence(line, summary))
    cleanup_failed = False
    for path in (debug_path, server_path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            cleanup_failed = True
    summary["raw_cleanup"] = "pending_directory_cleanup" if cleanup_failed else "removed"
    try:
        execution.tracker.record_grok_diagnostic(execution.token, summary)
    except (StaleRun, ValueError, OSError, sqlite3.Error):
        logging.getLogger(__name__).warning("Unable to store Grok diagnostic summary")


@contextmanager
def capture(execution, directory, enabled):
    if not enabled:
        yield None
        return
    debug_path = directory / "grok-debug.log"
    server_path = directory / "workbench-files.events"
    evidence = {"debug_path": debug_path, "server_path": server_path, "command": None}
    try:
        yield evidence
    finally:
        record_diagnostic(execution, **evidence)
