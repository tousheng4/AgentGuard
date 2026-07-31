from __future__ import annotations

import asyncio
import hmac
from collections.abc import AsyncIterator

import httpx
import websockets
from fastapi import HTTPException, Request, WebSocket, status
from fastapi.responses import StreamingResponse
from websockets.asyncio.client import ClientConnection
from websockets.typing import Origin

from agentguard.config import AppSettings, IngressSettings
from agentguard.ingress.headers import filter_request_headers, filter_response_headers
from agentguard.server.auth import API_KEY_HEADER
from agentguard.server.sandbox.errors import (
    SandboxEndpointUnavailableError,
    SandboxNotFoundError,
    SandboxRuntimeError,
)
from agentguard.server.sandbox.service import EndpointPurpose, SandboxRuntime


class RequestTooLargeError(RuntimeError):
    pass


async def _limited_request_body(
    request: Request,
    max_request_bytes: int,
) -> AsyncIterator[bytes]:
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_request_bytes:
            raise RequestTooLargeError(
                f"request body exceeds {max_request_bytes} bytes"
            )
        yield chunk


async def _stream_response(response: httpx.Response) -> AsyncIterator[bytes]:
    try:
        if response.is_stream_consumed:
            yield response.content
            return
        async for chunk in response.aiter_raw():
            yield chunk
    finally:
        await response.aclose()


def _set_forwarded_headers(headers: dict[str, str], request: Request) -> None:
    headers["X-Forwarded-Proto"] = request.url.scheme
    inbound_host = request.headers.get("host")
    if inbound_host:
        headers["X-Forwarded-Host"] = inbound_host
    if request.client:
        headers["X-Forwarded-For"] = request.client.host


def _target_url(endpoint: str, full_path: str) -> str:
    base = endpoint.rstrip("/")
    path = full_path.lstrip("/")
    return f"http://{base}/{path}" if path else f"http://{base}"


async def proxy_http_request(
    request: Request,
    runtime: SandboxRuntime,
    settings: IngressSettings,
    sandbox_id: str,
    port: int,
    full_path: str,
) -> StreamingResponse:
    if not settings.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    try:
        endpoint = runtime.endpoint(
            sandbox_id,
            port,
            purpose=EndpointPurpose.PROXY,
        )
    except SandboxNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SandboxEndpointUnavailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SandboxRuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    headers = filter_request_headers(request.headers, endpoint.headers)
    _set_forwarded_headers(headers, request)
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > settings.max_request_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"request body exceeds {settings.max_request_bytes} bytes",
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid Content-Length") from exc

    client: httpx.AsyncClient = request.app.state.ingress_http_client
    target = _target_url(endpoint.endpoint, full_path)
    body = (
        _limited_request_body(request, settings.max_request_bytes)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}
        else None
    )
    backend_request = client.build_request(
        request.method,
        target,
        params=request.url.query or None,
        headers=headers,
        content=body,
    )

    try:
        response = await client.send(backend_request, stream=True)
    except RequestTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except httpx.ConnectTimeout as exc:
        raise HTTPException(status_code=504, detail="sandbox connection timed out") from exc
    except httpx.ConnectError as exc:
        raise HTTPException(status_code=502, detail="could not connect to sandbox") from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="sandbox request timed out") from exc

    return StreamingResponse(
        _stream_response(response),
        status_code=response.status_code,
        headers=filter_response_headers(response.headers),
    )


async def _relay_client_to_backend(
    client: WebSocket,
    backend: ClientConnection,
) -> None:
    while True:
        message = await client.receive()
        if message["type"] == "websocket.disconnect":
            await backend.close(code=message.get("code", 1000))
            return
        if message.get("text") is not None:
            await backend.send(message["text"])
        elif message.get("bytes") is not None:
            await backend.send(message["bytes"])


async def _relay_backend_to_client(
    backend: ClientConnection,
    client: WebSocket,
) -> None:
    while True:
        payload = await backend.recv()
        if isinstance(payload, bytes):
            await client.send_bytes(payload)
        else:
            await client.send_text(payload)


def _websocket_authorized(websocket: WebSocket, settings: AppSettings) -> bool:
    api_key = settings.server.api_key
    if api_key is None:
        return True
    supplied = websocket.headers.get(API_KEY_HEADER)
    return bool(supplied and hmac.compare_digest(supplied, api_key))


async def proxy_websocket_request(
    websocket: WebSocket,
    runtime: SandboxRuntime,
    settings: AppSettings,
    sandbox_id: str,
    port: int,
    full_path: str,
) -> None:
    if not settings.ingress.enabled or not settings.ingress.websocket_enabled:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    if not _websocket_authorized(websocket, settings):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    try:
        endpoint = runtime.endpoint(
            sandbox_id,
            port,
            purpose=EndpointPurpose.PROXY,
        )
    except SandboxRuntimeError:
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    headers = filter_request_headers(websocket.headers, endpoint.headers)
    for key in list(headers):
        if key.lower().startswith("sec-websocket-") or key.lower() == "origin":
            headers.pop(key)
    target = _target_url(endpoint.endpoint, full_path).replace("http://", "ws://", 1)
    if websocket.url.query:
        target = f"{target}?{websocket.url.query}"
    subprotocols = list(websocket.scope.get("subprotocols", []))
    raw_origin = websocket.headers.get("origin")
    origin = Origin(raw_origin) if raw_origin else None

    try:
        async with websockets.connect(
            target,
            additional_headers=headers or None,
            subprotocols=subprotocols or None,
            origin=origin,
            open_timeout=settings.ingress.connect_timeout_seconds,
            max_size=None,
        ) as backend:
            await websocket.accept(subprotocol=backend.subprotocol)
            client_task = asyncio.create_task(
                _relay_client_to_backend(websocket, backend)
            )
            backend_task = asyncio.create_task(
                _relay_backend_to_client(backend, websocket)
            )
            done, pending = await asyncio.wait(
                {client_task, backend_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
    except asyncio.CancelledError:
        return
    except (OSError, TimeoutError, websockets.WebSocketException):
        try:
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        except RuntimeError:
            pass
