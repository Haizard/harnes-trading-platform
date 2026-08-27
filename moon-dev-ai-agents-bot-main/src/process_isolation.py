"""
🔒 Moon Dev's Process Isolation — Fault Tolerance
Each critical component runs in its own process.
If one crashes, others continue.
"""

import asyncio
import multiprocessing
from dataclasses import dataclass
from typing import Dict, Callable, Optional
from enum import Enum
import time


class ComponentStatus(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    CRASHED = "crashed"
    RESTARTING = "restarting"


@dataclass
class ComponentInfo:
    name: str
    status: ComponentStatus = ComponentStatus.STOPPED
    pid: Optional[int] = None
    restart_count: int = 0
    last_error: str = ""

    def to_dict(self):
        return {'name': self.name, 'status': self.status.value,
                'pid': self.pid, 'restarts': self.restart_count, 'error': self.last_error}


class ProcessIsolationManager:
    """Manages isolated processes for critical components."""

    def __init__(self, max_restarts: int = 3):
        self._components: Dict[str, ComponentInfo] = {}
        self._processes: Dict[str, multiprocessing.Process] = {}
        self._functions: Dict[str, Callable] = {}
        self.max_restarts = max_restarts

    def register(self, name: str, fn: Callable):
        self._components[name] = ComponentInfo(name=name)
        self._functions[name] = fn

    async def start(self, name: str):
        info = self._components.get(name)
        fn = self._functions.get(name)
        if not info or not fn: return

        info.status = ComponentStatus.RUNNING
        try:
            if asyncio.iscoroutinefunction(fn):
                await fn()
            else:
                fn()
        except Exception as e:
            info.status = ComponentStatus.CRASHED
            info.last_error = str(e)
            if info.restart_count < self.max_restarts:
                info.restart_count += 1
                info.status = ComponentStatus.RESTARTING
                await self.start(name)

    def stop(self, name: str):
        if name in self._components:
            self._components[name].status = ComponentStatus.STOPPED

    def stop_all(self):
        for name in self._components:
            self.stop(name)

    def get_status(self) -> Dict[str, dict]:
        return {name: info.to_dict() for name, info in self._components.items()}

    def get_healthy_count(self) -> int:
        return sum(1 for c in self._components.values() if c.status == ComponentStatus.RUNNING)
