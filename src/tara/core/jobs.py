"""A small in-process job board for work that outlives one conversational turn.

Bounded (running jobs and kept results), per-owner, with TTL, cancellation and honest state: a job that never ran to
completion is never reported as completed, and a restart loses in-memory jobs (documented limitation).
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .metrics import Metrics

STATES = ("queued", "running", "completed", "failed", "cancelled")


class JobLimitReached(RuntimeError):
    """Too many background jobs are already running for this board."""


@dataclass
class Job:
    job_id: str
    owner: str
    kind: str
    state: str = "queued"
    created: float = 0.0
    updated: float = 0.0
    result: Any = None
    error: str = ""
    task: asyncio.Task[Any] | None = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        out: dict[str, Any] = {"job_id": self.job_id, "kind": self.kind, "state": self.state}
        if self.state == "completed":
            out["result"] = self.result
        if self.state == "failed":
            out["error"] = self.error
        return out


def _alive(pid: str) -> bool:
    try:
        os.kill(int(pid), 0)
    except (ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True
    return True


class JobBoard:
    def __init__(self, *, max_running: int = 2, ttl_s: float = 1800.0, timeout_s: float = 60.0, max_kept: int = 50,
                 metrics: Metrics | None = None, clock: Callable[[], float] = time.monotonic, journal: Path | None = None):
        self.max_running, self.ttl_s, self.timeout_s, self.max_kept = max_running, ttl_s, timeout_s, max_kept
        self.metrics = metrics or Metrics()
        self._clock = clock
        self._jobs: dict[str, Job] = {}
        self.journal = journal
        self.interrupted: list[str] = []          # kinds of jobs a previous run started and never finished (it was restarted or killed)
        self._recover()

    # A restart loses in-memory jobs. The board writes the jobs it has open to a small file, so the next run can say honestly that
    # something was cut off (it cannot resume it). Entries are keyed by process id; only dead processes count as interrupted.
    def _read(self) -> dict[str, dict[str, str]]:
        if self.journal is None:
            return {}
        try:
            data = json.loads(self.journal.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict[str, dict[str, str]]) -> None:
        if self.journal is None:
            return
        try:
            self.journal.parent.mkdir(parents=True, exist_ok=True)
            self.journal.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass

    def _recover(self) -> None:
        data = self._read()
        keep: dict[str, dict[str, str]] = {}
        for pid, jobs in data.items():
            if pid != str(os.getpid()) and _alive(pid):
                keep[pid] = jobs                      # another run is still going: not ours to report
            elif pid != str(os.getpid()):
                self.interrupted += [k.split(":")[0] for k in jobs.values()]
        if self.journal is not None:
            self._write(keep)

    def _journal_set(self, job: Job, open_: bool) -> None:
        if self.journal is None:
            return
        data = self._read()
        mine = data.setdefault(str(os.getpid()), {})
        if open_:
            mine[job.job_id] = job.kind
        else:
            mine.pop(job.job_id, None)
        if not mine:
            data.pop(str(os.getpid()), None)
        self._write(data)

    @property
    def active(self) -> int:
        return sum(1 for j in self._jobs.values() if j.state in ("queued", "running"))

    def _prune(self) -> None:
        now = self._clock()
        for jid in [k for k, j in self._jobs.items() if j.state not in ("queued", "running") and now - j.updated > self.ttl_s]:
            self._jobs.pop(jid, None)
        while len(self._jobs) > self.max_kept:
            done = [j for j in self._jobs.values() if j.state not in ("queued", "running")]
            if not done:
                break
            self._jobs.pop(min(done, key=lambda j: j.updated).job_id, None)

    def start(self, owner: str, kind: str, work: Callable[[], Awaitable[Any]]) -> Job:
        """Must be called on the event loop (an `async def` tool handler), never from a worker thread."""
        self._prune()
        if self.active >= self.max_running:
            self.metrics.inc("jobs_rejected")
            raise JobLimitReached("too many background jobs are running")
        now = self._clock()
        job = Job(secrets.token_hex(4), owner, kind, "queued", now, now)
        self._jobs[job.job_id] = job
        job.task = asyncio.create_task(self._run(job, work), name=f"job:{job.job_id}")
        self.metrics.inc("jobs_started")
        self._journal_set(job, True)
        return job

    async def _run(self, job: Job, work: Callable[[], Awaitable[Any]]) -> None:
        job.state, job.updated = "running", self._clock()
        try:
            job.result = await asyncio.wait_for(work(), self.timeout_s)
            job.state = "completed"
            self.metrics.inc("jobs_completed")
        except asyncio.CancelledError:
            job.state = "cancelled"
            self.metrics.inc("jobs_cancelled")
            raise
        except TimeoutError:
            job.state, job.error = "failed", "timed out"
            self.metrics.inc("jobs_failed")
        except Exception as exc:  # noqa: BLE001 - a job failure is recorded, never raised into the voice path
            job.state, job.error = "failed", type(exc).__name__
            self.metrics.inc("jobs_failed")
        finally:
            job.updated = self._clock()
            self._journal_set(job, False)

    def get(self, owner: str, job_id: str) -> Job | None:
        j = self._jobs.get(job_id)
        return j if j is not None and j.owner == owner else None       # another owner's job does not exist for you

    def list(self, owner: str) -> list[Job]:
        return [j for j in self._jobs.values() if j.owner == owner]

    def cancel(self, owner: str, job_id: str) -> bool:
        j = self.get(owner, job_id)
        if j is None or j.state not in ("queued", "running") or j.task is None:
            return False
        j.task.cancel()
        return True

    async def drain(self, timeout_s: float) -> int:
        """Let running jobs finish (up to `timeout_s`) before shutdown, so "make the minutes" followed by the end of the meeting does not
        throw the document away. Returns how many were still running afterwards."""
        tasks = [j.task for j in self._jobs.values() if j.task is not None and not j.task.done()]
        if tasks and timeout_s > 0:
            await asyncio.wait(tasks, timeout=timeout_s)
        return sum(1 for t in tasks if not t.done())

    async def close(self) -> None:
        tasks = [j.task for j in self._jobs.values() if j.task is not None and not j.task.done()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
