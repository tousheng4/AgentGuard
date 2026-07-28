from __future__ import annotations

import json
import math
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from http.client import HTTPConnection
from pathlib import Path
from typing import Any
from uuid import uuid4

from docker.errors import DockerException, ImageNotFound, NotFound  # type: ignore[import-not-found]

import docker
from agentguard.constants import DEFAULT_EXECD_PORT
from agentguard.server.sandbox.errors import (
    SandboxEndpointUnavailableError,
    SandboxNotFoundError,
    SandboxRuntimeError,
    SandboxStateConflictError,
)
from agentguard.server.sandbox.injector import BOOTSTRAP_PATH, DockerRuntimeInjector
from agentguard.server.sandbox.models import (
    CreateSandboxRequest,
    RenewSandboxExpirationResponse,
    SandboxEndpoint,
    SandboxInfo,
    SandboxListResponse,
    SandboxState,
    SandboxStatus,
)
from agentguard.server.sandbox.service import RuntimeCapabilities

EXECD_PORT = DEFAULT_EXECD_PORT
SANDBOX_ID_LABEL = "agentguard.sandbox.id"
MANAGED_LABEL = "agentguard.managed"
IMAGE_LABEL = "agentguard.sandbox.image"
CREATED_AT_LABEL = "agentguard.sandbox.created_at"
EXPIRES_AT_LABEL = "agentguard.sandbox.expires_at"
ENTRYPOINT_LABEL = "agentguard.sandbox.entrypoint"
METADATA_LABEL = "agentguard.sandbox.metadata"
RESOURCE_LIMITS_LABEL = "agentguard.sandbox.resource_limits"
EXPOSED_PORTS_LABEL = "agentguard.sandbox.exposed_ports"


SandboxLifecycleError = SandboxRuntimeError


class DockerSandboxRuntime:
    def __init__(
        self,
        default_image: str | None = None,
        *,
        docker_client: Any | None = None,
        data_dir: str | Path | None = None,
        execd_ready_timeout_seconds: float = 5.0,
        bind_host: str = "127.0.0.1",
    ) -> None:
        self._default_image = default_image or os.environ.get(
            "AGENTGUARD_SANDBOX_IMAGE",
            "agentguard-sandbox:latest",
        )
        self._client: Any = docker_client or docker.from_env()  # type: ignore[attr-defined]
        self._runtime = DockerRuntimeInjector()
        self._execd_ready_timeout_seconds = execd_ready_timeout_seconds
        self._bind_host = bind_host
        self._expiration_lock = threading.RLock()
        self._expiration_timers: dict[str, threading.Timer] = {}
        self._expirations: dict[str, datetime] = {}
        root = Path(data_dir or os.environ.get("AGENTGUARD_DATA_DIR", "data"))
        self._expiration_store = root / "sandbox-expirations.json"
        self._load_expiration_store()
        self.restore_expirations()

    @property
    def name(self) -> str:
        return "docker"

    @property
    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            pause_resume=True,
            direct_endpoints=True,
            expiration=True,
            runtime_injection=True,
        )

    def close(self) -> None:
        with self._expiration_lock:
            timers = list(self._expiration_timers.values())
            self._expiration_timers.clear()
        for timer in timers:
            timer.cancel()

    def create(self, request: CreateSandboxRequest) -> SandboxInfo:
        sandbox_id = str(uuid4())
        image = request.image or self._default_image
        created_at = datetime.now(UTC)
        expires_at = (
            created_at + timedelta(seconds=request.timeout_seconds)
            if request.timeout_seconds is not None
            else None
        )
        ports = sorted({EXECD_PORT, *request.exposed_ports})
        labels = self._build_labels(
            sandbox_id,
            image,
            request,
            created_at,
            expires_at,
            ports,
        )
        port_bindings = {f"{port}/tcp": (self._bind_host, None) for port in ports}
        limits = request.resource_limits
        container = None

        try:
            self._ensure_image(image)
            container = self._client.containers.create(
                image=image,
                command=request.entrypoint,
                entrypoint=[BOOTSTRAP_PATH],
                detach=True,
                init=True,
                working_dir="/workspace",
                user="10001:10001",
                environment={
                    "HOME": "/tmp",
                    "PYTHONPATH": "/opt",
                    **request.env,
                },
                ports=port_bindings,
                mem_limit=f"{limits.memory_mb}m",
                nano_cpus=int(limits.cpu * 1_000_000_000),
                pids_limit=limits.pids,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                labels=labels,
            )
            self._runtime.inject(container)
            container.start()
            container.reload()
            self._wait_for_execd(container)
        except SandboxLifecycleError:
            self._cleanup_failed_container(container)
            raise
        except ImageNotFound as exc:
            self._cleanup_failed_container(container)
            raise SandboxLifecycleError(f"Sandbox image '{image}' not found") from exc
        except DockerException as exc:
            self._cleanup_failed_container(container)
            raise SandboxLifecycleError(f"Failed to create sandbox: {exc}") from exc

        if expires_at is not None:
            self._schedule_expiration(sandbox_id, expires_at)
        return self._container_to_sandbox(container, sandbox_id)

    def list_sandboxes(
        self,
        *,
        states: list[SandboxState] | None = None,
        metadata: dict[str, str] | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> SandboxListResponse:
        try:
            containers = self._client.containers.list(
                all=True,
                filters={"label": MANAGED_LABEL},
            )
        except DockerException as exc:
            raise SandboxLifecycleError(f"Failed to list sandboxes: {exc}") from exc

        items: list[SandboxInfo] = []
        for container in containers:
            try:
                container.reload()
            except NotFound:
                continue
            labels = container.attrs.get("Config", {}).get("Labels") or {}
            sandbox_id = labels.get(SANDBOX_ID_LABEL)
            if not sandbox_id:
                continue
            sandbox = self._container_to_sandbox(container, sandbox_id)
            if states and sandbox.state not in states:
                continue
            if metadata and any(
                sandbox.metadata.get(key) != value
                for key, value in metadata.items()
            ):
                continue
            items.append(sandbox)

        items.sort(key=lambda item: item.created_at, reverse=True)
        total_items = len(items)
        total_pages = math.ceil(total_items / page_size) if total_items else 0
        start = (page - 1) * page_size
        return SandboxListResponse(
            items=items[start : start + page_size],
            page=page,
            page_size=page_size,
            total_items=total_items,
            total_pages=total_pages,
        )

    def get(self, sandbox_id: str) -> SandboxInfo:
        container = self._get_container(sandbox_id)
        container.reload()
        return self._container_to_sandbox(container, sandbox_id)

    def delete(self, sandbox_id: str) -> None:
        container = self._get_container(sandbox_id)
        try:
            container.remove(force=True)
        except NotFound as exc:
            raise SandboxNotFoundError(f"Sandbox '{sandbox_id}' not found") from exc
        except DockerException as exc:
            raise SandboxLifecycleError(f"Failed to delete sandbox: {exc}") from exc
        finally:
            self._remove_expiration(sandbox_id)

    def pause(self, sandbox_id: str) -> None:
        container = self._get_container(sandbox_id)
        container.reload()
        if self._state_for_container(container) != SandboxState.RUNNING:
            raise SandboxStateConflictError(
                f"Sandbox '{sandbox_id}' is not running"
            )
        try:
            container.pause()
        except DockerException as exc:
            raise SandboxLifecycleError(f"Failed to pause sandbox: {exc}") from exc

    def resume(self, sandbox_id: str) -> None:
        container = self._get_container(sandbox_id)
        container.reload()
        if self._state_for_container(container) != SandboxState.PAUSED:
            raise SandboxStateConflictError(
                f"Sandbox '{sandbox_id}' is not paused"
            )
        try:
            container.unpause()
        except DockerException as exc:
            raise SandboxLifecycleError(f"Failed to resume sandbox: {exc}") from exc

    def renew_expiration(
        self,
        sandbox_id: str,
        timeout_seconds: int,
    ) -> RenewSandboxExpirationResponse:
        self._get_container(sandbox_id)
        expires_at = datetime.now(UTC) + timedelta(seconds=timeout_seconds)
        self._schedule_expiration(sandbox_id, expires_at)
        return RenewSandboxExpirationResponse(expires_at=expires_at)

    def endpoint(self, sandbox_id: str, port: int) -> SandboxEndpoint:
        if port < 1 or port > 65535:
            raise SandboxLifecycleError("port must be between 1 and 65535")

        container = self._get_container(sandbox_id)
        container.reload()
        labels = container.attrs.get("Config", {}).get("Labels") or {}
        exposed_ports = self._load_json_list(labels.get(EXPOSED_PORTS_LABEL))
        if port not in exposed_ports:
            raise SandboxEndpointUnavailableError(
                f"Port {port} is not exposed by sandbox '{sandbox_id}'"
            )
        ports = container.attrs.get("NetworkSettings", {}).get("Ports") or {}
        bindings = ports.get(f"{port}/tcp") or []
        if not bindings:
            raise SandboxEndpointUnavailableError(
                f"Sandbox '{sandbox_id}' has no binding for port {port}"
            )

        binding = bindings[0]
        host_ip = binding.get("HostIp") or "127.0.0.1"
        if host_ip in {"0.0.0.0", "::"}:
            host_ip = "127.0.0.1"
        return SandboxEndpoint(endpoint=f"{host_ip}:{binding['HostPort']}")

    def restore_expirations(self) -> None:
        try:
            containers = self._client.containers.list(
                all=True,
                filters={"label": MANAGED_LABEL},
            )
        except DockerException:
            return

        now = datetime.now(UTC)
        managed_ids: set[str] = set()
        for container in containers:
            labels = container.attrs.get("Config", {}).get("Labels") or {}
            sandbox_id = labels.get(SANDBOX_ID_LABEL)
            if not sandbox_id:
                continue
            managed_ids.add(sandbox_id)
            expires_at = self._expirations.get(sandbox_id)
            if expires_at is None:
                expires_at = self._parse_datetime(labels.get(EXPIRES_AT_LABEL))
            if expires_at is None:
                continue
            if expires_at <= now:
                self._expire(sandbox_id)
            else:
                self._schedule_expiration(sandbox_id, expires_at)

        stale_ids = set(self._expirations) - managed_ids
        for sandbox_id in stale_ids:
            self._remove_expiration(sandbox_id)

    def _ensure_image(self, image: str) -> None:
        try:
            self._client.images.get(image)
        except ImageNotFound:
            self._client.images.pull(image)

    def _wait_for_execd(
        self,
        container: Any,
        timeout_seconds: float | None = None,
    ) -> None:
        timeout_seconds = timeout_seconds or self._execd_ready_timeout_seconds
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            container.reload()
            if self._state_for_container(container) in {
                SandboxState.STOPPED,
                SandboxState.FAILED,
            }:
                logs = container.logs(tail=20).decode("utf-8", errors="replace")
                raise SandboxLifecycleError(
                    "Sandbox runtime stopped before execd became ready"
                    + (f": {logs.strip()}" if logs.strip() else "")
                )
            ports = container.attrs.get("NetworkSettings", {}).get("Ports") or {}
            bindings = ports.get(f"{EXECD_PORT}/tcp") or []
            if bindings:
                binding = bindings[0]
                host = binding.get("HostIp") or "127.0.0.1"
                if host in {"0.0.0.0", "::"}:
                    host = "127.0.0.1"
                connection = HTTPConnection(
                    host,
                    int(binding["HostPort"]),
                    timeout=0.2,
                )
                try:
                    connection.request("GET", "/ping")
                    response = connection.getresponse()
                    response.read()
                    if response.status == 200:
                        return
                except (OSError, TimeoutError):
                    pass
                finally:
                    connection.close()
            time.sleep(0.05)
        raise SandboxLifecycleError(
            f"Sandbox execd did not become ready within {timeout_seconds} seconds"
        )

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

    def _container_to_sandbox(self, container: Any, sandbox_id: str) -> SandboxInfo:
        attrs = container.attrs
        labels = attrs.get("Config", {}).get("Labels") or {}
        state = self._state_for_container(container)
        state_data = attrs.get("State", {}) or {}
        created_at = self._parse_datetime(labels.get(CREATED_AT_LABEL))
        if created_at is None:
            created_at = self._parse_datetime(attrs.get("Created")) or datetime.now(UTC)
        expires_at = self._expirations.get(sandbox_id)
        if expires_at is None:
            expires_at = self._parse_datetime(labels.get(EXPIRES_AT_LABEL))
        reason = None
        message = None
        if state == SandboxState.FAILED:
            reason = "container_failed"
            message = state_data.get("Error") or "Container failed"
        elif state == SandboxState.STOPPED:
            reason = "container_stopped"
            message = f"Container exited with code {state_data.get('ExitCode', 0)}"

        resource_data = self._load_json_dict(labels.get(RESOURCE_LIMITS_LABEL))
        from agentguard.server.sandbox.models import SandboxResourceLimits

        resource_limits = SandboxResourceLimits.model_validate(resource_data or {})
        exposed_ports = self._load_json_list(labels.get(EXPOSED_PORTS_LABEL))
        return SandboxInfo(
            id=sandbox_id,
            image=labels.get(IMAGE_LABEL) or attrs.get("Config", {}).get("Image", ""),
            state=state,
            status=SandboxStatus(
                state=state,
                reason=reason,
                message=message,
                last_transition_at=self._last_transition_at(state_data),
            ),
            metadata={
                str(key): str(value)
                for key, value in self._load_json_dict(
                    labels.get(METADATA_LABEL)
                ).items()
            },
            created_at=created_at,
            expires_at=expires_at,
            entrypoint=self._load_string_list(labels.get(ENTRYPOINT_LABEL)),
            resource_limits=resource_limits,
            exposed_ports=exposed_ports,
        )

    @staticmethod
    def _state_for_container(container: Any) -> SandboxState:
        state = container.attrs.get("State", {}) or {}
        if state.get("OOMKilled") or state.get("Dead") or state.get("Error"):
            return SandboxState.FAILED
        if state.get("Paused"):
            return SandboxState.PAUSED
        status = state.get("Status")
        if status == "running":
            return SandboxState.RUNNING
        if status in {"created", "restarting"}:
            return SandboxState.PENDING
        return SandboxState.STOPPED

    def _schedule_expiration(self, sandbox_id: str, expires_at: datetime) -> None:
        delay = max(0.0, (expires_at - datetime.now(UTC)).total_seconds())
        timer = threading.Timer(delay, self._expire, args=(sandbox_id,))
        timer.daemon = True
        with self._expiration_lock:
            previous = self._expiration_timers.pop(sandbox_id, None)
            if previous:
                previous.cancel()
            self._expirations[sandbox_id] = expires_at
            self._expiration_timers[sandbox_id] = timer
            self._save_expiration_store()
        timer.start()

    def _expire(self, sandbox_id: str) -> None:
        with self._expiration_lock:
            expires_at = self._expirations.get(sandbox_id)
        if expires_at is not None and expires_at > datetime.now(UTC):
            self._schedule_expiration(sandbox_id, expires_at)
            return
        try:
            container = self._get_container(sandbox_id)
            container.remove(force=True)
        except (SandboxNotFoundError, DockerException):
            pass
        finally:
            self._remove_expiration(sandbox_id)

    def _remove_expiration(self, sandbox_id: str) -> None:
        with self._expiration_lock:
            timer = self._expiration_timers.pop(sandbox_id, None)
            if timer:
                timer.cancel()
            self._expirations.pop(sandbox_id, None)
            self._save_expiration_store()

    def _load_expiration_store(self) -> None:
        try:
            payload = json.loads(self._expiration_store.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        if not isinstance(payload, dict):
            return
        for sandbox_id, raw_value in payload.items():
            parsed = self._parse_datetime(raw_value)
            if parsed is not None:
                self._expirations[str(sandbox_id)] = parsed

    def _save_expiration_store(self) -> None:
        self._expiration_store.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._expiration_store.with_suffix(".tmp")
        payload = {
            sandbox_id: expires_at.isoformat()
            for sandbox_id, expires_at in self._expirations.items()
        }
        temporary.write_text(json.dumps(payload, sort_keys=True))
        temporary.replace(self._expiration_store)

    @staticmethod
    def _cleanup_failed_container(container: Any | None) -> None:
        if container is None:
            return
        try:
            container.remove(force=True)
        except DockerException:
            pass

    @staticmethod
    def _build_labels(
        sandbox_id: str,
        image: str,
        request: CreateSandboxRequest,
        created_at: datetime,
        expires_at: datetime | None,
        exposed_ports: list[int],
    ) -> dict[str, str]:
        return {
            MANAGED_LABEL: "true",
            SANDBOX_ID_LABEL: sandbox_id,
            IMAGE_LABEL: image,
            CREATED_AT_LABEL: created_at.isoformat(),
            EXPIRES_AT_LABEL: expires_at.isoformat() if expires_at else "",
            ENTRYPOINT_LABEL: json.dumps(request.entrypoint),
            METADATA_LABEL: json.dumps(request.metadata, sort_keys=True),
            RESOURCE_LIMITS_LABEL: request.resource_limits.model_dump_json(),
            EXPOSED_PORTS_LABEL: json.dumps(exposed_ports),
        }

    @staticmethod
    def _load_json_dict(raw_value: Any) -> dict[str, Any]:
        try:
            value = json.loads(raw_value or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _load_json_list(raw_value: Any) -> list[int]:
        try:
            value = json.loads(raw_value or "[]")
        except (TypeError, json.JSONDecodeError):
            return []
        if not isinstance(value, list):
            return []
        result: list[int] = []
        for item in value:
            try:
                result.append(int(item))
            except (TypeError, ValueError):
                continue
        return result

    @staticmethod
    def _load_string_list(raw_value: Any) -> list[str]:
        try:
            value = json.loads(raw_value or "[]")
        except (TypeError, json.JSONDecodeError):
            return []
        if not isinstance(value, list):
            return []
        return [str(item) for item in value]

    @staticmethod
    def _parse_datetime(raw_value: Any) -> datetime | None:
        if not raw_value:
            return None
        try:
            parsed = datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @classmethod
    def _last_transition_at(cls, state: dict[str, Any]) -> datetime | None:
        for key in ("FinishedAt", "StartedAt"):
            parsed = cls._parse_datetime(state.get(key))
            if parsed is not None and parsed.year > 1:
                return parsed
        return None


# Backward-compatible import while callers migrate to the runtime terminology.
DockerSandboxLifecycle = DockerSandboxRuntime
