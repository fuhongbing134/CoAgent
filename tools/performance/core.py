"""Pure accounting for the controlled workbench experiment; no product imports."""
import math
import threading
import time
from collections import Counter

ALLOCATIONS = dict(trial=6, warm=20, stable=324, open=48, cross=20,
                   C2=9, C3=4, C4=5, C5=10, C6=4)


def appointments(d, factor, window=20.0, cap=24):
    if not all(isinstance(x, (int, float)) and math.isfinite(x) and x > 0
               for x in (d, factor, window)) or type(cap) is not int or cap < 1:
        raise ValueError("positive finite schedule required")
    rate = factor / d
    times = [k / rate for k in range(cap) if k / rate < window]
    return times, min(window, len(times) / rate)


class Budget:
    def __init__(self, seconds=720, allocations=None, starts=470, active=6):
        self.allocations = ALLOCATIONS if allocations is None else allocations
        self.max_starts, self.max_active = starts, active
        self.calls_active = 0
        self.unconfirmed = 0
        self.deadline = time.monotonic() + seconds
        self.counts = Counter()
        self.starts = 0
        self.active = 0
        self.job_calls = Counter()
        self.lock = threading.Lock()
        self.stopped = threading.Event()

    def check(self):
        if self.stopped.is_set() or time.monotonic() >= self.deadline:
            raise RuntimeError("experiment stopped or deadline reached")

    def request(self, group):
        with self.lock:
            self.check()
            if group not in self.allocations or self.counts[group] >= self.allocations[group]:
                raise RuntimeError("request allocation exhausted")
            self.counts[group] += 1

    def start(self, job=None):
        with self.lock:
            self.check()
            if self.starts >= self.max_starts or self.active >= self.max_active or (job and self.job_calls[job] >= 5):
                self.stopped.set()
                raise RuntimeError("process or job budget exhausted")
            self.starts += 1
            self.active += 1
            self.calls_active += 1
            if job:
                self.job_calls[job] += 1

    def finish(self):
        with self.lock:
            self.active -= 1
            self.calls_active -= 1

    def settle(self, exit_evidence):
        """Wrapper has returned; uncertain process ownership is still reserved."""
        if exit_evidence in ("confirmed", "not_started"):
            self.finish()
        else:
            with self.lock:
                self.calls_active -= 1
                self.unconfirmed += 1
                self.stopped.set()


def classify(http, message_status=None):
    if http == 409:
        return "rejected"
    if http != 202:
        return "http_error"
    return {"completed": "completed", "failed": "failed", "cancelled": "cancelled"}.get(
        message_status, "unfinished")


def summarize(records, start, cutoff, observation_end=None):
    if cutoff <= start:
        raise ValueError("positive safety window required")
    end = min(cutoff, observation_end if observation_end is not None else cutoff)
    sends = [r["send"] for r in records if r.get("send") is not None and r["send"] <= end]
    first = min(sends) if sends else None
    duration = end-first if first is not None and end > first else None
    final = Counter(r["classification"] for r in records)
    states, late = Counter(), Counter()
    successful = []
    for r in records:
        seen = r.get("seen", r.get("observed", r.get("ack")))
        is_late = seen is not None and seen > cutoff
        state = r["classification"]
        if is_late:
            late[state] += 1
            state = "unfinished" if r.get("send") is not None and r["send"] <= cutoff else "not_sent"
        states[state] += 1
        r.update(cutoff_classification=state, late=is_late, final_classification=r["classification"])
        if state == "completed" and seen is not None and seen <= end and r.get("send") is not None:
            successful.append(seen-r["send"])
    successful.sort()
    completed = len(successful)
    return dict(counts=dict(states), final_counts=dict(final), late_counts=dict(late),
                samples=completed, computable=duration is not None and bool(records),
                completed_per_second=completed/duration if duration is not None and records else None,
                p50=successful[math.ceil(.5*completed)-1] if completed else None,
                p95=successful[math.ceil(.95*completed)-1] if completed else None,
                denominator_seconds=duration, safety_cutoff=cutoff, observation_end=end,
                send_window_seconds=max(sends)-first if sends else None,
                drain_seconds=end-max(sends) if sends else None)
