"""Explicit local-only controlled driver. No imports of the product until --run."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
from urllib.request import Request, ProxyHandler, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError

from core import Budget, appointments, classify, summarize
from harness import Harness
from resources import sample


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise RuntimeError("redirect forbidden")


class Driver:
    def __init__(self, harness, port):
        self.h = harness
        self.url = "http://127.0.0.1:"+str(port)
        self.records = []
        self.lock = threading.Lock()
        self.serial = 0

    def http(self, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        request = Request(self.url+path, data=data, headers={"Content-Type": "application/json"})
        opener = build_opener(ProxyHandler({}), NoRedirect())
        try:
            with opener.open(request, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def conversation(self):
        with self.lock:
            self.serial += 1
            return "fixture-"+str(self.serial)

    def one(self, group, agent="codex", conversation=None, delay=.75,
            fault="none", collaboration=False, due=None, cutoff=None, dropped=False):
        conversation = conversation or self.conversation()
        self.h.modes[conversation] = (delay, fault)
        record = dict(group=group, agent=agent, conversation=conversation,
                      due=due, send=None, fault=fault, classification="unfinished")
        try:
            self.h.budget.request(group)
            if dropped:
                record["classification"] = "generator_dropped"
                return record
            if cutoff is not None and time.perf_counter() >= cutoff:
                record["classification"] = "not_sent"
                return record
            record["send"] = time.perf_counter()
            code, data = self.http("/api/messages", dict(conversation_id=conversation,
                content=("@"+agent+" " if collaboration else "")+"controlled fixture",
                recipients=[agent]))
            record.update(http=code, ack=time.perf_counter(), response=data)
            if code != 202:
                record["classification"] = classify(code)
                return record
            message = data["assistant_message_ids"][agent]
            record["message"] = message
            job = "entry-"+str(message)
            cancel_sent = False
            deadline = min(time.monotonic()+15, self.h.budget.deadline)
            if cutoff is not None:
                deadline = min(deadline, time.monotonic()+max(0, cutoff-time.perf_counter()))
            while time.monotonic() < deadline:
                if fault == "cancel" and not cancel_sent:
                    with self.h.lock:
                        started = any(e.get("message") == message and e["event"] == "process_before_resume" for e in self.h.events)
                    if started:
                        record["cancel_response"] = self.http("/api/stop", dict(message_id=message))
                        cancel_sent = True
                code2, observed = self.http("/api/messages?conversation_id="+conversation)
                if code2 != 200:
                    record["observation_error"] = code2
                    break
                matching = next((m for m in observed["messages"] if m["id"] == message), None)
                terminal = matching and matching["status"] != "pending"
                with self.h.web.ACTIVE_RUNS_LOCK:
                    terminal = terminal and message not in self.h.web.ACTIVE_RUNS
                if terminal and collaboration and fault != "protocol":
                    with sqlite3.connect(self.h.db) as db:
                        row = db.execute("SELECT phase FROM collab_lifecycle WHERE job_id=?", (job,)).fetchone()
                    terminal = row and row[0] in ("completed", "failed", "stopped", "blocked")
                    record["job_phase"] = row[0] if row else "missing"
                if terminal:
                    record.update(seen=time.perf_counter(), status=matching["status"], content=matching["content"])
                    record["classification"] = classify(202, matching["status"])
                    with self.h.lock:
                        results = [e for e in self.h.events if e.get("message") == message and e["event"] == "result_observed"]
                    if results and results[-1]["outcome"] in ("timeout", "cancelled"):
                        record["classification"] = results[-1]["outcome"]
                    if record["classification"] == "completed" and "fixture-message-"+str(message) not in matching["content"]:
                        record["classification"] = "correlation_error"
                        self.h.budget.stopped.set()
                    if collaboration and fault != "protocol" and record.get("job_phase") != "completed":
                        record["classification"] = "failed"
                    break
                time.sleep(.1)
        except Exception as error:
            record.update(classification="driver_error", error=type(error).__name__+": "+str(error))
            self.h.budget.stopped.set()
        finally:
            record["observed"] = time.perf_counter()
            if record["classification"] == "unfinished":
                self.h.budget.stopped.set()
            with self.lock:
                self.records.append(record)
        return record

    def batch(self, group, count, concurrency, delay=.75, agents=("codex",)):
        start = time.perf_counter()
        cutoff = start + 60
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(self.one, group, agents[i % len(agents)], delay=delay, cutoff=cutoff) for i in range(count)]
            records = [future.result() for future in futures]
        return dict(group=group, delay=delay, concurrency=concurrency, observed_seconds=time.perf_counter()-start,
                    metrics=summarize(records, start, cutoff, time.perf_counter()))

    def open_window(self, d, factor):
        schedule, horizon = appointments(d, factor)
        before = len(self.records)
        start = time.perf_counter()
        cutoff = start+60
        pending, futures, errors = set(), [], []
        with ThreadPoolExecutor(max_workers=6) as pool:
            for offset in schedule:
                due = start+offset
                remaining = due-time.perf_counter()
                if remaining > 0:
                    time.sleep(remaining)
                pending = {f for f in pending if not f.done()}
                if len(pending) >= 6:
                    self.one("open", due=due, cutoff=cutoff, dropped=True)
                else:
                    future = pool.submit(self.one, "open", due=due, cutoff=cutoff)
                    pending.add(future)
                    futures.append((future, due))
            for future, due in futures:
                try:
                    future.result()
                except Exception as error:
                    errors.append(str(error))
                    with self.lock:
                        self.records.append(dict(group="open", due=due, send=None,
                            classification="driver_error", observed=time.perf_counter(), error=str(error)))
                    self.h.budget.stopped.set()
        if errors:
            raise RuntimeError("open workers failed: "+"; ".join(errors))
        return dict(factor=factor, D=d, reservations=len(schedule), planned_horizon=horizon,
                    elapsed=time.perf_counter()-start,
                    metrics=summarize(self.records[before:], start, cutoff, time.perf_counter()))

    def suite(self, smoke):
        summaries = []
        # Six trial requests also exercise all three actual worker adapters.
        summaries.append(self.batch("trial", 3, 1, agents=("codex", "deepseek-harness", "grok")))
        summaries.append(self.batch("trial", 3, 3, agents=("codex", "deepseek-harness", "grok")))
        if smoke:
            return summaries
        d_samples = []
        for delay in (0, .75):
            for concurrency in (1, 3, 6):
                self.batch("warm", concurrency, concurrency, delay)
                for _ in range(3):
                    before = len(self.records)
                    summaries.append(self.batch("stable", 18, concurrency, delay))
                    if delay == .75 and concurrency == 1:
                        d_samples.extend(r["seen"]-r["send"] for r in self.records[before:]
                                         if r["classification"] == "completed")
        if d_samples:
            d = statistics.median(d_samples)
            summaries.extend(self.open_window(d, factor) for factor in (.5, 1.25))
        summaries.append(self.batch("cross", 20, 2, agents=("codex", "grok")))
        for _ in range(3):
            conversation = self.conversation()
            with ThreadPoolExecutor(max_workers=3) as pool:
                holder = pool.submit(self.one, "C2", conversation=conversation)
                end = time.monotonic()+3
                while time.monotonic() < end:
                    with self.h.web.ACTIVE_RUNS_LOCK:
                        messages = {m for m, run in self.h.web.ACTIVE_RUNS.items() if run["conversation_id"] == conversation}
                    with self.h.lock:
                        admitted = any(e["event"] == "admitted" and e.get("message") in messages for e in self.h.events)
                    if admitted:
                        break
                    time.sleep(.01)
                if not admitted:
                    raise RuntimeError("C2 holder not observed; competition precondition missing")
                rivals = [pool.submit(self.one, "C2", conversation=conversation) for _ in range(2)]
                [f.result() for f in [holder, *rivals]]
        for _ in range(2):
            conversation = self.conversation()
            with ThreadPoolExecutor(max_workers=2) as pool:
                [f.result() for f in [pool.submit(self.one, "C3", agent=a, conversation=conversation) for a in ("codex", "grok")]]
        self.one("C4", collaboration=True)
        for agents in (("codex", "codex"), ("codex", "grok")):
            with ThreadPoolExecutor(max_workers=2) as pool:
                [f.result() for f in [pool.submit(self.one, "C4", agent=a, collaboration=True) for a in agents]]
        for fault in ("cancel", "nonzero", "timeout", "protocol"):
            conversation = self.conversation()
            self.one("C5", conversation=conversation, delay=30 if fault in ("cancel", "timeout") else .75,
                     fault=fault, collaboration=fault == "protocol")
            self.one("C5", conversation=conversation)
        summaries.append(dict(skipped="C5 unknown and subsequent probe", reason="no safe real unknown injection; never forge evidence"))
        for agent in ("codex", "grok"):
            conversation = self.conversation()
            self.one("C6", agent=agent, conversation=conversation)
            self.one("C6", agent=agent, conversation=conversation)
        return summaries


def audit(report):
    """Conservative reconciliation; missing runtime evidence never becomes success."""
    failures = []
    records, runs, events = report.get("records", []), report.get("runs", []), report.get("events", [])
    for summary in report.get("summaries", []):
        if "metrics" in summary and not summary["metrics"].get("computable"):
            failures.append("performance observation window not computable")
    by_message = {}
    for run in runs:
        by_message.setdefault(run["source_message_id"], []).append(run)
        if run["state"] in ("pending", "running", "quiescing"):
            failures.append("unsettled run: "+run["run_id"])
    for record in records:
        message = record.get("message")
        if record.get("http") == 202:
            matches = by_message.get(message, [])
            if len(matches) != 1:
                failures.append("accepted request lacks unique run: "+str(message))
            if record["classification"] in ("unfinished", "driver_error", "correlation_error", "http_error"):
                failures.append("unresolved request: "+str(message))
        expected = {"none": "completed", "nonzero": "failed", "cancel": "cancelled", "timeout": "timeout", "protocol": "failed"}[record.get("fault", "none")]
        if record["group"] != "C2" and record["classification"] not in (expected, "generator_dropped"):
            failures.append("unexpected terminal classification: "+str(message))
        if record.get("http") == 409 and message in by_message:
            failures.append("rejected request launched")
    if report.get("mode") == "collaboration-smoke" and (len(records) != 1 or not events):
        failures.append("missing collaboration smoke evidence")
    starts = [e for e in events if e["event"] == "launch_requested"]
    observed = {e["message"]: e for e in events if e["event"] == "result_observed"}
    for start in starts:
        result = observed.get(start["message"])
        if not result or result["exit_evidence"] not in ("confirmed", "not_started"):
            failures.append("exit not confirmed: "+str(start["message"]))
    if len({e["message"] for e in starts}) != len(starts):
        failures.append("duplicate worker execution")
    session_domains = {}
    for e in starts:
        record = next((r for r in records if r.get("message") == e["message"]), None)
        if record:
            domain = (record["conversation"], e["agent"])
            previous = session_domains.setdefault(e["session"], domain)
            if previous != domain:
                failures.append("session crossed execution domains")
    for conversation in {r["conversation"] for r in records if r["group"] == "C6"}:
        messages = {r["message"] for r in records if r["conversation"] == conversation and "message" in r}
        sessions = {e["session"] for e in starts if e["message"] in messages}
        modes = [json.loads(r["config_snapshot"])["launch_mode"] for r in runs if r["source_message_id"] in messages]
        if len(sessions) != 1 or sorted(modes) != ["new", "resume"]:
            failures.append("C6 new/resume binding not observed")
    intervals = []
    for e in events:
        if e["event"] == "process_before_resume" and e["message"] in observed:
            origin = next(x for x in starts if x["message"] == e["message"])
            intervals.append((e["ns"], observed[e["message"]]["ns"], origin))
    for i, (a, b, origin) in enumerate(intervals):
        for c, d, other in intervals[i+1:]:
            if max(a, c) < min(b, d) and origin.get("job") and other.get("job"):
                same_job = origin["job"] == other["job"]
                locked = origin["role"] != "entry" and other["role"] != "entry" and origin["agent"] == other["agent"]
                if same_job or locked:
                    failures.append("unexpected overlap in serial collaboration path")
    c2 = [r for r in records if r["group"] == "C2"]
    if c2 and sum(r["classification"] == "rejected" for r in c2) != 6:
        failures.append("C2 expected six refusals not observed")
    jobs = {e["job"] for e in starts if e.get("role") == "entry"}
    if report.get("mode") == "collaboration-smoke" and (len(jobs) != 1 or len(starts) != 5):
        failures.append("collaboration smoke requires exactly one five-call job")
    for job in jobs:
        calls = [e for e in starts if e.get("job") == job]
        # The intentionally invalid entry must have no downstream calls.
        entry = next(e for e in calls if e["role"] == "entry")
        negative = any(r.get("message") == entry["message"] and r["group"] == "C5" for r in records)
        expected = 1 if negative else 5
        if len(calls) != expected:
            failures.append("unexpected job call count: "+job)
        if not negative:
            from collections import Counter
            if Counter(e["role"] for e in calls) != dict(entry=1, collaborator=2, owner=1, final=1):
                failures.append("unexpected job roles: "+job)
            receipts = [e for e in events if e["event"] == "protocol_receipt_returned" and e.get("job") == job and e["disposition"] == "accepted"]
            if len(receipts) != 4:
                failures.append("missing accepted protocol receipts: "+job)
    return dict(failures=failures, scope="request/run uniqueness, output marker, exit evidence and protocol receipts",
                limitations=["session content and exact overlap need reviewer examination of runs/events",
                             "rejected HTTP responses expose no run ID; absence requires domain-level inspection"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", choices=("smoke", "controlled", "collaboration-smoke"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output must not already exist")
    if sys.platform != "win32":
        parser.error("real Windows Job evidence requires Windows")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Keep the isolated root for investigation if cleanup cannot confirm release.
    root = Path(tempfile.mkdtemp(prefix="workbench-controlled-"))
    tempfile.tempdir = str(root)
    harness = Harness(root)
    if args.run == "collaboration-smoke":
        harness.budget = Budget(seconds=60, allocations={"C4": 1}, starts=5, active=1)
    initial = sample([])
    if initial["gaps"] or initial["available"] < 2*1024**3:
        raise RuntimeError("insufficient memory or resource query unavailable before startup")
    web = harness.prepare()
    report = dict(mode=args.run, limitations=["fixture is not real CLI", "entry acceptance timestamp unavailable", "no precise root exit timestamp"])
    with web.startup_server(0) as server:
        with sqlite3.connect(harness.db) as db:
            for agent in web.AGENT_IDS:
                db.execute("INSERT OR IGNORE INTO agents VALUES (?,?,?,?,?,?,?)", (agent, agent, "fixture", "fixture", "fixture", web.utc_now(), web.utc_now()))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        driver = Driver(harness, server.server_address[1])
        monitor_done = threading.Event()
        resource_rows = []
        def monitor():
            low = 0
            while not monitor_done.wait(.5):
                with harness.lock:
                    created = {e["message"]: e.get("pid") for e in harness.events if e["event"] == "process_after_create"}
                    ended = {e["message"] for e in harness.events if e["event"] == "result_observed"}
                try:
                    row = sample([pid for message, pid in created.items() if message not in ended and pid])
                    row.update(ns=time.perf_counter_ns(), db_bytes=harness.db.stat().st_size,
                               wal_bytes=Path(str(harness.db)+"-wal").stat().st_size if Path(str(harness.db)+"-wal").exists() else 0)
                    resource_rows.append(row)
                    low = low+1 if row["rss"] >= 1.5*1024**3 or row["available"] < 2*1024**3 else 0
                    if low >= 2 or time.monotonic() >= harness.budget.deadline:
                        harness.budget.stopped.set()
                except OSError as error:
                    resource_rows.append(dict(error=str(error), ns=time.perf_counter_ns()))
                    harness.budget.stopped.set()
                if harness.budget.stopped.is_set():
                    with web.ACTIVE_RUNS_LOCK:
                        ids = list(web.ACTIVE_RUNS)
                    for message in ids:
                        web.stop_active_run(message)
        watcher = threading.Thread(target=monitor, daemon=True)
        watcher.start()
        try:
            if args.run == "collaboration-smoke":
                driver.one("C4", collaboration=True)
                report["summaries"] = [dict(validation="one collaboration job; separate budget")]
            else:
                report["summaries"] = driver.suite(args.run == "smoke")
        except Exception as error:
            report["error"] = type(error).__name__+": "+str(error)
        finally:
            monitor_done.set()
            watcher.join(timeout=15)
            report["resources"] = resource_rows
            report["clean_exit"] = harness.cleanup() and not watcher.is_alive()
            server.shutdown()
            thread.join(timeout=5)
            report.update(records=driver.records, events=harness.events,
                          request_counts=dict(harness.budget.counts), process_starts=harness.budget.starts,
                          wrapper_calls_active=harness.budget.calls_active,
                          unconfirmed_ownership=harness.budget.unconfirmed)
            with sqlite3.connect(harness.db) as db:
                db.row_factory = sqlite3.Row
                report["runs"] = [dict(row) for row in db.execute("SELECT * FROM execution_runs")]
    report["correctness"] = audit(report)
    report["resource_scope"] = "cohosted driver+service and known fixture roots (fixture spawns no descendants); not pure service RSS"
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report.get("clean_exit") and "error" not in report and not report["correctness"]["failures"] else 1


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    raise SystemExit(main())
