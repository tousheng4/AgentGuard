from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path


class ExpirationManager:
    """Persist and schedule sandbox expiration without backend-specific logic."""

    def __init__(
        self,
        store_path: Path,
        on_expire: Callable[[str], None],
    ) -> None:
        self._store_path = store_path
        self._on_expire = on_expire
        self._lock = threading.RLock()
        self._timers: dict[str, threading.Timer] = {}
        self._expirations: dict[str, datetime] = {}
        self._load()

    def get(self, sandbox_id: str) -> datetime | None:
        with self._lock:
            return self._expirations.get(sandbox_id)

    def ids(self) -> set[str]:
        with self._lock:
            return set(self._expirations)

    def schedule(self, sandbox_id: str, expires_at: datetime) -> None:
        delay = max(0.0, (expires_at - datetime.now(UTC)).total_seconds())
        timer = threading.Timer(delay, self._expire, args=(sandbox_id,))
        timer.daemon = True
        with self._lock:
            previous = self._timers.pop(sandbox_id, None)
            if previous:
                previous.cancel()
            self._expirations[sandbox_id] = expires_at
            self._timers[sandbox_id] = timer
            self._save()
        timer.start()

    def remove(self, sandbox_id: str) -> None:
        with self._lock:
            timer = self._timers.pop(sandbox_id, None)
            if timer:
                timer.cancel()
            self._expirations.pop(sandbox_id, None)
            self._save()

    def close(self) -> None:
        with self._lock:
            timers = list(self._timers.values())
            self._timers.clear()
        for timer in timers:
            timer.cancel()

    def _expire(self, sandbox_id: str) -> None:
        expires_at = self.get(sandbox_id)
        if expires_at is not None and expires_at > datetime.now(UTC):
            self.schedule(sandbox_id, expires_at)
            return
        try:
            self._on_expire(sandbox_id)
        finally:
            self.remove(sandbox_id)

    def _load(self) -> None:
        try:
            payload = json.loads(self._store_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        if not isinstance(payload, dict):
            return
        for sandbox_id, raw_value in payload.items():
            parsed = self._parse_datetime(raw_value)
            if parsed is not None:
                self._expirations[str(sandbox_id)] = parsed

    def _save(self) -> None:
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._store_path.with_suffix(".tmp")
        payload = {
            sandbox_id: expires_at.isoformat()
            for sandbox_id, expires_at in self._expirations.items()
        }
        temporary.write_text(json.dumps(payload, sort_keys=True))
        temporary.replace(self._store_path)

    @staticmethod
    def _parse_datetime(raw_value: object) -> datetime | None:
        if not raw_value:
            return None
        try:
            parsed = datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
