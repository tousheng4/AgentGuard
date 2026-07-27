from __future__ import annotations

import io
import tarfile
from pathlib import Path
from typing import Any

import pytest

from agentguard.server.sandbox.docker import (
    BOOTSTRAP_PATH,
    DockerSandboxLifecycle,
    SandboxLifecycleError,
)
from agentguard.server.sandbox.models import CreateSandboxRequest, SandboxState


class FakeContainer:
    def __init__(self, manager: FakeContainers, kwargs: dict[str, Any]) -> None:
        self._manager = manager
        self.kwargs = kwargs
        self.id = "container-1"
        self.removed = False
        self.archive: bytes | None = None
        self.attrs: dict[str, Any] = {
            "Created": "2026-07-27T00:00:00Z",
            "Config": {
                "Image": kwargs["image"],
                "Labels": kwargs["labels"],
            },
            "State": {
                "Status": "created",
                "Running": False,
                "Paused": False,
                "StartedAt": "0001-01-01T00:00:00Z",
            },
            "NetworkSettings": {"Ports": {}},
        }

    def put_archive(self, path: str, data: bytes) -> bool:
        assert path == "/"
        self.archive = data
        return True

    def start(self) -> None:
        self.attrs["State"].update(
            {
                "Status": "running",
                "Running": True,
                "StartedAt": "2026-07-27T00:00:01Z",
            }
        )
        self.attrs["NetworkSettings"]["Ports"] = {
            port: [{"HostIp": "127.0.0.1", "HostPort": str(45000 + index)}]
            for index, port in enumerate(self.kwargs["ports"])
        }

    def reload(self) -> None:
        return

    def remove(self, force: bool = False) -> None:
        assert force
        self.removed = True

    def pause(self) -> None:
        self.attrs["State"].update({"Paused": True, "Status": "paused"})

    def unpause(self) -> None:
        self.attrs["State"].update({"Paused": False, "Status": "running"})

    def logs(self, tail: int | None = None) -> bytes:
        del tail
        return b""


class FakeContainers:
    def __init__(self) -> None:
        self.items: list[FakeContainer] = []
        self.last_create_kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> FakeContainer:
        self.last_create_kwargs = kwargs
        container = FakeContainer(self, kwargs)
        self.items.append(container)
        return container

    def list(
        self,
        all: bool = False,
        filters: dict[str, str] | None = None,
    ) -> list[FakeContainer]:
        del all
        containers = [item for item in self.items if not item.removed]
        label_filter = (filters or {}).get("label")
        if label_filter and "=" in label_filter:
            key, value = label_filter.split("=", 1)
            containers = [
                item
                for item in containers
                if item.attrs["Config"]["Labels"].get(key) == value
            ]
        return containers


class FakeImages:
    def get(self, image: str) -> object:
        return object()

    def pull(self, image: str) -> object:
        return object()


class FakeDockerClient:
    def __init__(self) -> None:
        self.containers = FakeContainers()
        self.images = FakeImages()


def test_docker_create_injects_runtime_and_maps_lifecycle(
    tmp_path: Path,
) -> None:
    client = FakeDockerClient()
    lifecycle = DockerSandboxLifecycle(
        docker_client=client,
        data_dir=tmp_path,
    )
    lifecycle._wait_for_execd = lambda container: None  # type: ignore[method-assign]

    sandbox = lifecycle.create(
        CreateSandboxRequest(
            image="python:3.12-slim",
            timeout_seconds=3600,
            entrypoint=["python", "-m", "http.server", "8080"],
            metadata={"task": "test"},
            exposed_ports=[8080],
        )
    )

    container = client.containers.items[0]
    kwargs = client.containers.last_create_kwargs
    assert kwargs is not None
    assert kwargs["entrypoint"] == [BOOTSTRAP_PATH]
    assert kwargs["command"] == ["python", "-m", "http.server", "8080"]
    assert kwargs["user"] == "10001:10001"
    assert kwargs["cap_drop"] == ["ALL"]
    assert set(kwargs["ports"]) == {"44772/tcp", "8080/tcp"}
    assert container.archive is not None
    with tarfile.open(fileobj=io.BytesIO(container.archive)) as archive:
        names = archive.getnames()
    assert "opt/agentguard-runtime/bootstrap.sh" in names
    assert "opt/agentguard/execd/server.py" in names

    assert sandbox.state == SandboxState.RUNNING
    assert sandbox.metadata == {"task": "test"}
    assert sandbox.exposed_ports == [8080, 44772]

    listed = lifecycle.list_sandboxes(metadata={"task": "test"})
    assert listed.total_items == 1

    lifecycle.pause(sandbox.id)
    assert lifecycle.get(sandbox.id).state == SandboxState.PAUSED
    lifecycle.resume(sandbox.id)
    assert lifecycle.get(sandbox.id).state == SandboxState.RUNNING

    lifecycle.delete(sandbox.id)
    assert container.removed
    assert lifecycle.list_sandboxes().total_items == 0


def test_docker_create_cleans_up_when_runtime_never_becomes_ready(
    tmp_path: Path,
) -> None:
    client = FakeDockerClient()
    lifecycle = DockerSandboxLifecycle(
        docker_client=client,
        data_dir=tmp_path,
    )

    def fail_readiness(container: FakeContainer) -> None:
        del container
        raise SandboxLifecycleError("execd failed")

    lifecycle._wait_for_execd = fail_readiness  # type: ignore[method-assign]

    with pytest.raises(SandboxLifecycleError, match="execd failed"):
        lifecycle.create(
            CreateSandboxRequest(
                image="unsupported:latest",
                timeout_seconds=None,
            )
        )

    assert client.containers.items[0].removed
