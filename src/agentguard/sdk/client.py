from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from agentguard.constants import DEFAULT_EXECD_PORT
from agentguard.sdk.files import FilesClient
from agentguard.server.sandbox.models import SandboxInfo, SandboxRunResult


@dataclass
class CommandsClient:
    endpoint: str
    timeout_seconds: float = 30.0
    transport: httpx.AsyncBaseTransport | None = None

    async def run(
        self,
        command: str,
        *,
        cwd: str = "/workspace",
        timeout_seconds: int = 30,
    ) -> SandboxRunResult:
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            transport=self.transport,
            trust_env=False,
        ) as client:
            response = await client.post(
                f"http://{self.endpoint}/command",
                json={
                    "command": command,
                    "cwd": cwd,
                    "timeout_seconds": timeout_seconds,
                },
            )
            response.raise_for_status()
            return SandboxRunResult.model_validate(response.json())


@dataclass
class Sandbox:
    id: str
    info: SandboxInfo
    commands: CommandsClient
    files: FilesClient
    _client: AgentGuardClient

    async def kill(self) -> None:
        await self._client.delete_sandbox(self.id)


class AgentGuardClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._transport = transport

    async def create_sandbox(
        self,
        image: str | None = None,
        *,
        timeout_seconds: int = 1800,
    ) -> Sandbox:
        async with httpx.AsyncClient(base_url=self.base_url, transport=self._transport) as client:
            create_response = await client.post(
                "/v1/sandboxes",
                json={
                    "image": image,
                    "timeout_seconds": timeout_seconds,
                },
            )
            create_response.raise_for_status()
            info = SandboxInfo.model_validate(create_response.json())

            endpoint_response = await client.get(
                f"/v1/sandboxes/{info.id}/endpoints/{DEFAULT_EXECD_PORT}"
            )
            endpoint_response.raise_for_status()
            endpoint = endpoint_response.json()["endpoint"]

        await self._wait_for_execd(endpoint)

        return Sandbox(
            id=info.id,
            info=info,
            commands=CommandsClient(endpoint=endpoint, transport=self._transport),
            files=FilesClient(endpoint=endpoint, transport=self._transport),
            _client=self,
        )

    async def _wait_for_execd(
        self,
        endpoint: str,
        *,
        attempts: int = 50,
        interval_seconds: float = 0.1,
    ) -> None:
        last_error: Exception | None = None
        async with httpx.AsyncClient(
            timeout=1.0,
            transport=self._transport,
            trust_env=False,
        ) as client:
            for attempt in range(attempts):
                try:
                    response = await client.get(f"http://{endpoint}/ping")
                    response.raise_for_status()
                    return
                except httpx.HTTPError as exc:
                    last_error = exc
                    if attempt + 1 < attempts:
                        await asyncio.sleep(interval_seconds)

        raise RuntimeError(f"execd at {endpoint} did not become ready") from last_error

    async def delete_sandbox(self, sandbox_id: str) -> None:
        async with httpx.AsyncClient(base_url=self.base_url, transport=self._transport) as client:
            response = await client.delete(f"/v1/sandboxes/{sandbox_id}")
            response.raise_for_status()
