from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import httpx

from agentguard.constants import DEFAULT_EXECD_PORT
from agentguard.sdk.execution import (
    Execution,
    ExecutionComplete,
    ExecutionError,
    ExecutionHandlers,
    OutputMessage,
)
from agentguard.sdk.files import FilesClient
from agentguard.server.sandbox.models import (
    RenewSandboxExpirationResponse,
    SandboxEndpoint,
    SandboxInfo,
    SandboxListResponse,
    SandboxResourceLimits,
)


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
        handlers: ExecutionHandlers | None = None,
    ) -> Execution:
        execution = Execution()
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=self.timeout_seconds,
                read=None,
                write=self.timeout_seconds,
                pool=self.timeout_seconds,
            ),
            transport=self.transport,
            trust_env=False,
        ) as client:
            async with client.stream(
                "POST",
                f"http://{self.endpoint}/command",
                json={
                    "command": command,
                    "cwd": cwd,
                    "timeout_seconds": timeout_seconds,
                },
                headers={
                    "Accept": "text/event-stream",
                    "Cache-Control": "no-cache",
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    event = self._decode_sse_line(line)
                    if event is not None:
                        await self._dispatch_event(execution, event, handlers)

        if execution.complete is None and execution.error is None:
            raise RuntimeError("execd command stream ended without a terminal event")
        return execution

    @staticmethod
    def _decode_sse_line(line: str) -> dict[str, Any] | None:
        if not line.strip() or line.startswith((":", "event:", "id:", "retry:")):
            return None
        data = line[5:].strip() if line.startswith("data:") else line
        decoded = json.loads(data)
        if not isinstance(decoded, dict):
            raise ValueError("SSE event must be a JSON object")
        return decoded

    @staticmethod
    async def _dispatch_event(
        execution: Execution,
        event: dict[str, Any],
        handlers: ExecutionHandlers | None,
    ) -> None:
        event_type = event.get("type")
        timestamp = int(event.get("timestamp", 0))

        if event_type == "init":
            execution.id = str(event.get("text", ""))
            if handlers and handlers.on_init:
                await handlers.on_init(execution.id)
        elif event_type in {"stdout", "stderr"}:
            message = OutputMessage(
                text=str(event.get("text", "")),
                timestamp=timestamp,
                is_error=event_type == "stderr",
            )
            if not (handlers and handlers.skip_accumulation):
                target = (
                    execution.logs.stderr
                    if event_type == "stderr"
                    else execution.logs.stdout
                )
                target.append(message)
            handler = (
                handlers.on_stderr
                if handlers and event_type == "stderr"
                else handlers.on_stdout if handlers else None
            )
            if handler:
                await handler(message)
        elif event_type == "error":
            error_data = event.get("error") or {}
            error = ExecutionError(
                name=str(error_data.get("ename", "")),
                value=str(error_data.get("evalue", "")),
                traceback=list(error_data.get("traceback") or []),
                timestamp=timestamp,
            )
            execution.error = error
            try:
                execution.exit_code = int(error.value)
            except ValueError:
                execution.exit_code = None
            if handlers and handlers.on_error:
                await handlers.on_error(error)
        elif event_type == "execution_complete":
            complete = ExecutionComplete(
                timestamp=timestamp,
                execution_time_in_millis=int(event.get("execution_time", 0)),
            )
            execution.complete = complete
            execution.exit_code = 0
            if handlers and handlers.on_execution_complete:
                await handlers.on_execution_complete(complete)


@dataclass
class Sandbox:
    id: str
    info: SandboxInfo
    commands: CommandsClient
    files: FilesClient
    _client: AgentGuardClient

    async def kill(self) -> None:
        await self._client.delete_sandbox(self.id)

    async def pause(self) -> None:
        await self._client.pause_sandbox(self.id)

    async def resume(self) -> None:
        await self._client.resume_sandbox(self.id)

    async def renew_expiration(
        self,
        *,
        timeout_seconds: int,
    ) -> RenewSandboxExpirationResponse:
        response = await self._client.renew_sandbox_expiration(
            self.id,
            timeout_seconds=timeout_seconds,
        )
        self.info.expires_at = response.expires_at
        return response

    async def get_endpoint(self, port: int) -> SandboxEndpoint:
        return await self._client.get_sandbox_endpoint(self.id, port)


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
        timeout_seconds: int | None = 1800,
        entrypoint: list[str] | None = None,
        env: dict[str, str] | None = None,
        metadata: dict[str, str] | None = None,
        resource_limits: SandboxResourceLimits | None = None,
        exposed_ports: list[int] | None = None,
    ) -> Sandbox:
        async with self._control_client() as client:
            create_response = await client.post(
                "/v1/sandboxes",
                json={
                    "image": image,
                    "timeout_seconds": timeout_seconds,
                    "entrypoint": entrypoint or ["tail", "-f", "/dev/null"],
                    "env": env or {},
                    "metadata": metadata or {},
                    "resource_limits": (
                        resource_limits or SandboxResourceLimits()
                    ).model_dump(),
                    "exposed_ports": exposed_ports or [],
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

    async def list_sandboxes(
        self,
        *,
        states: list[str] | None = None,
        metadata: dict[str, str] | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> SandboxListResponse:
        params: list[tuple[str, str | int | float | bool | None]] = [
            ("page", page),
            ("page_size", page_size),
        ]
        params.extend(("state", state) for state in states or [])
        if metadata:
            from urllib.parse import urlencode

            params.append(("metadata", urlencode(metadata)))
        async with self._control_client() as client:
            response = await client.get("/v1/sandboxes", params=params)
            response.raise_for_status()
            return SandboxListResponse.model_validate(response.json())

    async def get_sandbox(self, sandbox_id: str) -> SandboxInfo:
        async with self._control_client() as client:
            response = await client.get(f"/v1/sandboxes/{sandbox_id}")
            response.raise_for_status()
            return SandboxInfo.model_validate(response.json())

    async def pause_sandbox(self, sandbox_id: str) -> None:
        await self._post_lifecycle_action(sandbox_id, "pause")

    async def resume_sandbox(self, sandbox_id: str) -> None:
        await self._post_lifecycle_action(sandbox_id, "resume")

    async def renew_sandbox_expiration(
        self,
        sandbox_id: str,
        *,
        timeout_seconds: int,
    ) -> RenewSandboxExpirationResponse:
        async with self._control_client() as client:
            response = await client.post(
                f"/v1/sandboxes/{sandbox_id}/renew-expiration",
                json={"timeout_seconds": timeout_seconds},
            )
            response.raise_for_status()
            return RenewSandboxExpirationResponse.model_validate(response.json())

    async def get_sandbox_endpoint(
        self,
        sandbox_id: str,
        port: int,
    ) -> SandboxEndpoint:
        async with self._control_client() as client:
            response = await client.get(
                f"/v1/sandboxes/{sandbox_id}/endpoints/{port}"
            )
            response.raise_for_status()
            return SandboxEndpoint.model_validate(response.json())

    async def _post_lifecycle_action(self, sandbox_id: str, action: str) -> None:
        async with self._control_client() as client:
            response = await client.post(f"/v1/sandboxes/{sandbox_id}/{action}")
            response.raise_for_status()

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
        async with self._control_client() as client:
            response = await client.delete(f"/v1/sandboxes/{sandbox_id}")
            response.raise_for_status()

    def _control_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=60.0,
            transport=self._transport,
            trust_env=False,
        )
