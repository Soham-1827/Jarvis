"""Process-wide limits for the chat API, keyed by device rather than by connection."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field

CHATS_PER_MINUTE = 20
FAILED_AUTHS_PER_MINUTE = 10
MAX_CONCURRENT_TURNS = 2


class SlidingWindow:
    def __init__(self, per_minute: int) -> None:
        self._per_minute = per_minute
        self._hits: deque[float] = deque()

    def allow(self) -> bool:
        now = time.monotonic()
        while self._hits and now - self._hits[0] > 60:
            self._hits.popleft()
        if len(self._hits) >= self._per_minute:
            return False
        self._hits.append(now)
        return True


@dataclass
class ApiLimits:
    chats_per_minute: int = CHATS_PER_MINUTE
    failed_auths: SlidingWindow = field(
        default_factory=lambda: SlidingWindow(FAILED_AUTHS_PER_MINUTE)
    )
    turns: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(MAX_CONCURRENT_TURNS)
    )
    _per_device: dict[str, SlidingWindow] = field(default_factory=dict)
    _busy: set[str] = field(default_factory=set)

    def allow_chat(self, device_id: str) -> bool:
        window = self._per_device.setdefault(device_id, SlidingWindow(self.chats_per_minute))
        return window.allow()

    def try_start(self, device_id: str) -> bool:
        """One in-flight turn per device, across all of its connections."""
        if device_id in self._busy:
            return False
        self._busy.add(device_id)
        return True

    def finish(self, device_id: str) -> None:
        self._busy.discard(device_id)
