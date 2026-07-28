from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from agentguard.config import (
    AppSettings,
    DockerRuntimeSettings,
    RuntimeSettings,
)
from agentguard.server.app import create_app
from agentguard.server.sandbox.docker import DockerSandboxRuntime
from agentguard.server.sandbox.factory import (
    create_sandbox_runtime,
    list_available_runtimes,
)
from agentguard.server.sandbox.service import RuntimeCapabilities


class StubRuntime:
    name = "stub"
    capabilities = RuntimeCapabilities()

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"Unexpected runtime operation: {name}")


def test_factory_builds_configured_docker_runtime(tmp_path: Path) -> None:
    settings = AppSettings(
        runtime=RuntimeSettings(type="docker"),
        docker=DockerRuntimeSettings(
            image="custom-sandbox:latest",
            data_dir=tmp_path,
            execd_ready_timeout_seconds=2.5,
            bind_host="0.0.0.0",
        ),
    )

    runtime = create_sandbox_runtime(settings)

    assert isinstance(runtime, DockerSandboxRuntime)
    assert runtime.name == "docker"
    assert runtime.capabilities.pause_resume
    assert list_available_runtimes() == ["docker"]
    runtime.close()


def test_app_uses_injected_runtime_and_closes_it() -> None:
    runtime = StubRuntime()
    application = create_app(runtime=runtime)  # type: ignore[arg-type]

    with TestClient(application) as client:
        response = client.get("/health")
        assert response.json() == {"status": "ok", "runtime": "stub"}
        assert not runtime.closed

    assert runtime.closed
