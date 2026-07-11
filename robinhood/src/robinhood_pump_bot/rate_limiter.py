from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional


@dataclass
class ApiKeyState:
    key: str
    second_hits: deque[float] = field(default_factory=deque)
    hour_hits: deque[float] = field(default_factory=deque)
    disabled_until: float = 0.0


class ApiKeyPool:
    """Round-robin API key pool with per-second/per-hour counters.

    If all keys are temporarily unavailable, acquire() waits until the soonest key
    becomes available. Empty key list is allowed and returns None for free APIs.
    """

    def __init__(self, keys: Iterable[str], per_second_limit: int, per_hour_limit: int, clock: Callable[[], float] | None = None):
        self._states = [ApiKeyState(k) for k in keys if k]
        self.per_second_limit = per_second_limit
        self.per_hour_limit = per_hour_limit
        self.clock = clock or time.time
        self._idx = 0
        self._lock = asyncio.Lock()

    def add_key(self, key: str) -> None:
        if key and key not in [s.key for s in self._states]:
            self._states.append(ApiKeyState(key))

    def remove_key(self, key: str) -> None:
        self._states = [s for s in self._states if s.key != key]
        self._idx = 0

    def mark_exhausted(self, key: Optional[str], seconds: int = 3600) -> None:
        if key is None:
            return
        now = self.clock()
        for state in self._states:
            if state.key == key:
                state.disabled_until = max(state.disabled_until, now + seconds)
                return

    async def acquire(self) -> Optional[str]:
        if not self._states:
            return None
        while True:
            async with self._lock:
                now = self.clock()
                best_wait = 1.0
                for _ in range(len(self._states)):
                    state = self._states[self._idx % len(self._states)]
                    self._idx += 1
                    self._prune(state, now)
                    wait = self._available_in(state, now)
                    if wait <= 0:
                        state.second_hits.append(now)
                        state.hour_hits.append(now)
                        return state.key
                    best_wait = min(best_wait, wait)
            await asyncio.sleep(max(0.05, min(best_wait, 1.0)))

    def _prune(self, state: ApiKeyState, now: float) -> None:
        while state.second_hits and now - state.second_hits[0] >= 1:
            state.second_hits.popleft()
        while state.hour_hits and now - state.hour_hits[0] >= 3600:
            state.hour_hits.popleft()

    def _available_in(self, state: ApiKeyState, now: float) -> float:
        if state.disabled_until > now:
            return state.disabled_until - now
        if len(state.second_hits) >= self.per_second_limit:
            return 1 - (now - state.second_hits[0])
        if len(state.hour_hits) >= self.per_hour_limit:
            return 3600 - (now - state.hour_hits[0])
        return 0.0
