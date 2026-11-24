"""Bounded, manual probes of the workbench process, never of Agent sandboxes."""

import copy
import os
import re
import socket
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError


TARGETS = ("https://github.com", "https://chatgpt.com")
CACHE_TTL_SECONDS = 300
PROBE_COOLDOWN_SECONDS = 30
MAX_CACHED_DIRECTORIES = 64
HTTP_TIMEOUT_SECONDS = 5
GIT_TIMEOUT_SECONDS = 8


def build_opener():
    from urllib.request import HTTPRedirectHandler, build_opener as make_opener

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    return make_opener(NoRedirect())


def error_kind(error, tls_error=()):
    reason = getattr(error, "reason", error)
    if isinstance(reason, (TimeoutError, socket.timeout, subprocess.TimeoutExpired)):
        return "timeout"
    if isinstance(reason, tls_error):
        return "tls_error"
    if isinstance(reason, PermissionError):
        return "permission_denied"
    if isinstance(reason, socket.gaierror):
        return "dns_error"
    return "connection_or_execution_error"


def run_git(directory, *args):
    env = os.environ.copy()
    env.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never", GIT_OPTIONAL_LOCKS="0",
               GIT_ASKPASS="", SSH_ASKPASS="")
    return subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-c", "core.askPass=",
         "-c", "credential.interactive=false", *args],
        cwd=directory, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT_SECONDS, shell=False,
    )


def git_error(result):
    # Do not return raw Git output: it can contain credential-bearing URLs.
    text = (result.stderr or "").lower()
    if any(word in text for word in ("authentication", "username", "credential")):
        return "authentication_failed_or_unavailable"
    if "not a git repository" in text:
        return "not_a_repository"
    if "permission denied" in text:
        return "permission_denied"
    return "git_failed_reason_undetermined"


def collect(directory):
    results = []

    def probe(name, target, method, action):
        started = time.monotonic()
        item = dict(name=name, target=str(target), method=method, environment="workbench",
                    source="measurement", checked_at=datetime.now(timezone.utc).isoformat())
        try:
            item.update(action())
        except Exception as error:
            item.update(status="failed", detail="temporary_file_cleanup_failed"
                        if isinstance(error, RuntimeError) and str(error) == "temporary_file_cleanup_failed"
                        else error_kind(error))
        item["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        results.append(item)

    def file_write():
        path = None
        try:
            with tempfile.NamedTemporaryFile(prefix=".capability-", suffix=".tmp", dir=directory,
                                             delete=False) as file:
                path = Path(file.name)
                file.write(b"workbench-probe")
            if path.read_bytes() != b"workbench-probe":
                raise OSError("Read-back mismatch")
        finally:
            if path is not None:
                try:
                    path.unlink()
                except OSError:
                    raise RuntimeError("temporary_file_cleanup_failed") from None
        return dict(status="passed", detail="temporary_file_created_read_and_removed")

    probe("workspace_write", directory, "temporary file: create/read/remove", file_write)

    def local_git():
        result = run_git(directory, "status", "--porcelain", "--untracked-files=no")
        return dict(status="passed" if result.returncode == 0 else "failed",
                    detail="repository_readable" if result.returncode == 0 else git_error(result))

    probe("git_read", directory, "git status (optional locks disabled)", local_git)
    for url in TARGETS:
        def http(url=url):
            try:
                import ssl
                from urllib.request import Request
            except (ImportError, OSError):
                return dict(status="failed", detail="host_native_dependency_failed_ssl")
            try:
                with build_opener().open(
                    Request(url, method="HEAD", headers={"User-Agent": "CoAgent-capability-probe"}),
                    timeout=HTTP_TIMEOUT_SECONDS,
                ) as response:
                    code = response.status
            except HTTPError as error:
                code = error.code
                error.close()
            except OSError as error:
                return dict(status="failed", detail=error_kind(error, ssl.SSLError))
            return dict(status="responded", http_status=code,
                        detail="HTTP response received; does not verify model/MCP APIs")
        probe("https", url, "HEAD, no redirects", http)

    def remote_git():
        result = run_git(directory, "remote", "get-url", "origin")
        url = result.stdout.strip()
        if result.returncode != 0:
            return dict(status="skipped", detail="origin_unavailable")
        if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?/?", url):
            return dict(status="skipped", detail="origin_must_be_plain_github_https_url")
        rewrites = run_git(directory, "config", "--get-regexp", r"^url\..*\.insteadof$")
        if rewrites.returncode != 1:
            return dict(status="skipped", detail="url_rewrites_present_or_configuration_unreadable")
        # Use the resolved, validated URL rather than a mutable remote alias.
        result = run_git(directory, "-c", "http.followRedirects=false", "ls-remote", url)
        return dict(status="passed" if result.returncode == 0 else "failed",
                    detail="remote_readable; push permission not tested" if result.returncode == 0
                    else git_error(result))

    probe("git_remote_read", "origin (validated GitHub HTTPS)", "git ls-remote; noninteractive", remote_git)
    return results


class ProbeManager:
    """One background job globally, directory-scoped cache, no polling side effects."""

    def __init__(self, collector=collect, clock=time.monotonic):
        self.collector = collector
        self.clock = clock
        self.lock = threading.Lock()
        self.cache = {}
        self.running = False

    def snapshot(self, directory):
        key = str(Path(directory).resolve())
        with self.lock:
            item = copy.deepcopy(self.cache.get(key, dict(state="not_tested", results=[])))
            item["expired"] = bool("finished" in item and self.clock() - item["finished"] > CACHE_TTL_SECONDS)
            item.pop("finished", None)
            item["working_directory"] = key
            item["agent_sandbox"] = {"state": "not_tested"}
            return item

    def start(self, directory):
        key = str(Path(directory).resolve())
        with self.lock:
            previous = self.cache.get(key, {})
            if previous.get("state") == "running":
                return
            if self.running:
                raise ValueError("Another capability probe is running; retry later.")
            if "finished" in previous and self.clock() - previous["finished"] < PROBE_COOLDOWN_SECONDS:
                raise ValueError(f"Wait {PROBE_COOLDOWN_SECONDS} seconds before repeating this probe.")
            if len(self.cache) >= MAX_CACHED_DIRECTORIES and key not in self.cache:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = dict(state="running", results=[])
            self.running = True
        try:
            threading.Thread(target=self._run, args=(key,), daemon=True).start()
        except Exception:
            with self.lock:
                self.running = False
                self.cache[key] = dict(state="failed", results=[], finished=self.clock())
            raise

    def _run(self, key):
        try:
            item = dict(state="completed", results=self.collector(Path(key)))
        except Exception:
            item = dict(state="failed", results=[], detail="probe_failed_reason_undetermined")
        with self.lock:
            item["finished"] = self.clock()
            self.cache[key] = item
            self.running = False
