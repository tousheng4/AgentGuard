import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentguard.server.sandbox.expiration import ExpirationManager


def test_expiration_manager_persists_and_removes_entries(tmp_path: Path) -> None:
    store = tmp_path / "expirations.json"
    manager = ExpirationManager(store, lambda sandbox_id: None)
    expires_at = datetime.now(UTC) + timedelta(hours=1)

    manager.schedule("sandbox-1", expires_at)

    assert manager.get("sandbox-1") == expires_at
    assert json.loads(store.read_text()) == {"sandbox-1": expires_at.isoformat()}

    manager.remove("sandbox-1")
    assert manager.get("sandbox-1") is None
    assert json.loads(store.read_text()) == {}
    manager.close()


def test_expiration_manager_invokes_backend_callback(tmp_path: Path) -> None:
    expired = threading.Event()
    expired_ids: list[str] = []

    def on_expire(sandbox_id: str) -> None:
        expired_ids.append(sandbox_id)
        expired.set()

    manager = ExpirationManager(tmp_path / "expirations.json", on_expire)
    manager.schedule("sandbox-1", datetime.now(UTC))

    assert expired.wait(timeout=1)
    assert expired_ids == ["sandbox-1"]
    assert manager.get("sandbox-1") is None
    manager.close()
