from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

from docker.errors import DockerException, ImageNotFound, NotFound  # type: ignore[import-not-found]

import docker
from agentguard.constants import DEFAULT_EXECD_PORT
from agentguard.server.sandbox.models import (
    CreateSandboxRequest,
    SandboxEndpoint,
    SandboxInfo,
    SandboxState,
)

EXECD_PORT = DEFAULT_EXECD_PORT
SANDBOX_ID_LABEL = "agentguard.sandbox.id"
MANAGED_LABEL = "agentguard.managed"


class SandboxLifecycleError(RuntimeError):
    pass


class SandboxNotFoundError(SandboxLifecycleError):
    pass


class DockerSandboxLifecycle:
    def __init__(self, default_image: str | None = None) -> None:
        self._default_image = default_image or os.environ.get(
            "AGENTGUARD_SANDBOX_IMAGE",
            "agentguard-sandbox:latest",
        )
        self._client: Any = docker.from_env()  # type: ignore[attr-defined]

    def create(self, request: CreateSandboxRequest) -> SandboxInfo:
        sandbox_id = str(uuid4())
        image = request.image or self._default_image

        try:
            container = self._client.containers.run(
                image=image,
                detach=True,
                working_dir="/workspace",
                ports={f"{EXECD_PORT}/tcp": ("127.0.0.1", None)},
                mem_limit="512m",
                nano_cpus=1_000_000_000,
                pids_limit=128,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                labels={
                    MANAGED_LABEL: "true",
                    SANDBOX_ID_LABEL: sandbox_id,
                    "agentguard.sandbox.image": image,
                },
            )
        except ImageNotFound as exc:
            raise SandboxLifecycleError(f"Sandbox image '{image}' not found") from exc
        except DockerException as exc:
            raise SandboxLifecycleError(f"Failed to create sandbox: {exc}") from exc

        container.reload()
        return SandboxInfo(id=sandbox_id, image=image, state=self._state_for_container(container))

    def get(self, sandbox_id: str) -> SandboxInfo:
        container = self._get_container(sandbox_id)
        labels = container.attrs.get("Config", {}).get("Labels") or {}
        image = labels.get("agentguard.sandbox.image") or container.attrs["Config"]["Image"]
        return SandboxInfo(id=sandbox_id, image=image, state=self._state_for_container(container))

    def delete(self, sandbox_id: str) -> None:
        container = self._get_container(sandbox_id)
        try:
            container.remove(force=True)
        except NotFound as exc:
            raise SandboxNotFoundError(f"Sandbox '{sandbox_id}' not found") from exc
        except DockerException as exc:
            raise SandboxLifecycleError(f"Failed to delete sandbox: {exc}") from exc

    def endpoint(self, sandbox_id: str, port: int) -> SandboxEndpoint:
        if port != EXECD_PORT:
            raise SandboxLifecycleError(f"Only execd port {EXECD_PORT} is supported in phase 1")

        container = self._get_container(sandbox_id)
        container.reload()
        ports = container.attrs.get("NetworkSettings", {}).get("Ports") or {}
        bindings = ports.get(f"{EXECD_PORT}/tcp") or []
        if not bindings:
            raise SandboxLifecycleError(f"Sandbox '{sandbox_id}' has no execd port binding")

        binding = bindings[0]
        host_ip = binding.get("HostIp") or "127.0.0.1"
        if host_ip == "0.0.0.0":
            host_ip = "127.0.0.1"
        host_port = binding["HostPort"]
        return SandboxEndpoint(endpoint=f"{host_ip}:{host_port}")

    def _get_container(self, sandbox_id: str) -> Any:
        try:
            containers = self._client.containers.list(
                all=True,
                filters={"label": f"{SANDBOX_ID_LABEL}={sandbox_id}"},
            )
        except DockerException as exc:
            raise SandboxLifecycleError(f"Failed to query sandbox: {exc}") from exc

        if not containers:
            raise SandboxNotFoundError(f"Sandbox '{sandbox_id}' not found")
        return containers[0]

    @staticmethod
    def _state_for_container(container: Any) -> SandboxState:
        state = (container.attrs.get("State", {}) or {}).get("Status")
        if state == "running":
            return SandboxState.RUNNING
        return SandboxState.STOPPED
