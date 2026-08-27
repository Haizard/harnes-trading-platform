"""
⏰ Moon Dev's Async Scheduler — Non-blocking Jobs
DSH Pattern: ctx.jobs — background jobs with cooperative cancellation.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Awaitable
from enum import Enum
from termcolor import cprint


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class JobResult:
    name: str
    status: JobStatus
    duration_ms: float = 0.0
    error: str = ""
    result: any = None

    def to_dict(self):
        return {'name': self.name, 'status': self.status.value,
                'duration_ms': round(self.duration_ms, 1), 'error': self.error}


@dataclass
class ScheduledJob:
    name: str
    fn: Callable
    interval_seconds: float
    enabled: bool = True
    last_run: float = 0.0
    run_count: int = 0
    status: JobStatus = JobStatus.PENDING


class AsyncScheduler:
    """Non-blocking job scheduler replacing time.sleep() loops."""

    def __init__(self):
        self._jobs: Dict[str, ScheduledJob] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._results: List[JobResult] = []

    def register(self, name: str, fn: Callable, interval_seconds: float):
        self._jobs[name] = ScheduledJob(name=name, fn=fn, interval_seconds=interval_seconds)

    def enable(self, name: str):
        if name in self._jobs: self._jobs[name].enabled = True

    def disable(self, name: str):
        if name in self._jobs: self._jobs[name].enabled = False

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self._running = False
        if self._task: self._task.cancel()

    async def _loop(self):
        while self._running:
            now = time.time()
            for job in self._jobs.values():
                if not job.enabled: continue
                if now - job.last_run >= job.interval_seconds:
                    await self._run_job(job)
            await asyncio.sleep(1)

    async def _run_job(self, job: ScheduledJob):
        job.status = JobStatus.RUNNING
        start = time.time()
        try:
            if asyncio.iscoroutinefunction(job.fn):
                await job.fn()
            else:
                job.fn()
            job.status = JobStatus.COMPLETED
        except Exception as e:
            job.status = JobStatus.FAILED
        job.last_run = time.time()
        job.run_count += 1
        self._results.append(JobResult(job.name, job.status, (time.time()-start)*1000))

    def get_status(self) -> dict:
        return {name: {'status': j.status.value, 'runs': j.run_count, 'enabled': j.enabled}
                for name, j in self._jobs.items()}

    async def run_now(self, name: str) -> JobResult:
        job = self._jobs.get(name)
        if not job: return JobResult(name, JobStatus.FAILED, error="Job not found")
        await self._run_job(job)
        return self._results[-1] if self._results else JobResult(name, JobStatus.FAILED)
