from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentguard.server.sandbox.models import (
    CreateSandboxRequest,
    RenewSandboxExpirationResponse,
    SandboxEndpoint,
    SandboxInfo,
    SandboxListResponse,
    SandboxState,
)


@dataclass(frozen=True)
class RuntimeCapabilities:
    pause_resume: bool = False
    direct_endpoints: bool = False
    expiration: bool = False
    runtime_injection: bool = False


@runtime_checkable
class SandboxRuntime(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def capabilities(self) -> RuntimeCapabilities: ...

    def create(self, request: CreateSandboxRequest) -> SandboxInfo: ...

    def get(self, sandbox_id: str) -> SandboxInfo: ...

    def list_sandboxes(
        self,
        *,
        states: list[SandboxState] | None = None,
        metadata: dict[str, str] | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> SandboxListResponse: ...

    def delete(self, sandbox_id: str) -> None: ...

    def pause(self, sandbox_id: str) -> None: ...

    def resume(self, sandbox_id: str) -> None: ...

    def renew_expiration(
        self,
        sandbox_id: str,
        timeout_seconds: int,
    ) -> RenewSandboxExpirationResponse: ...

    def endpoint(self, sandbox_id: str, port: int) -> SandboxEndpoint: ...

    def close(self) -> None: ...
