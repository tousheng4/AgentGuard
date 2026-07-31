from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi.testclient import TestClient

from agentguard.config import (
    AppSettings,
    IngressSettings,
    ServerSettings,
)
from agentguard.server.app import create_app
from agentguard.server.sandbox.models import SandboxEndpoint
from agentguard.server.sandbox.service import EndpointPurpose, RuntimeCapabilities


class IngressRuntime:
    name = "test"
    capabilities = RuntimeCapabilities(direct_endpoints=True)

    def __init__(self) -> None:
        self.purposes: list[EndpointPurpose] = []
        self.closed = False

    def endpoint(
        self,
        sandbox_id: str,
        port: int,
        *,
        purpose: EndpointPurpose = EndpointPurpose.PUBLIC,
    ) -> SandboxEndpoint:
        assert sandbox_id == "sandbox-1"
        assert port == 8080
        self.purposes.append(purpose)
        return SandboxEndpoint(
            endpoint="backend.local:8080",
            headers={"X-Endpoint-Route": "sandbox-1-8080"},
        )

    def close(self) -> None:
        self.closed = True

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"unexpected runtime operation: {name}")


def ingress_settings(**kwargs: Any) -> AppSettings:
    return AppSettings(
        ingress=IngressSettings(
            enabled=True,
            public_address="ingress.local:8000",
            **kwargs,
        )
    )


def test_endpoint_can_return_server_proxy_address() -> None:
    runtime = IngressRuntime()
    app = create_app(settings=ingress_settings(), runtime=runtime)  # type: ignore[arg-type]

    with TestClient(app) as client:
        response = client.get(
            "/v1/sandboxes/sandbox-1/endpoints/8080",
            params={"use_server_proxy": "true"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "endpoint": "ingress.local:8000/v1/sandboxes/sandbox-1/proxy/8080"
    }
    assert runtime.purposes == [EndpointPurpose.PUBLIC]


def test_http_ingress_streams_request_and_filters_sensitive_headers() -> None:
    runtime = IngressRuntime()
    captured: dict[str, Any] = {}

    async def backend(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = await request.aread()
        return httpx.Response(
            201,
            headers={
                "X-Backend": "yes",
                "Connection": "X-Backend-Hop",
                "X-Backend-Hop": "secret",
            },
            content=b"proxied",
        )

    app = create_app(
        settings=ingress_settings(),
        runtime=runtime,  # type: ignore[arg-type]
        ingress_transport=httpx.MockTransport(backend),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/sandboxes/sandbox-1/proxy/8080/api/run",
            params={"q": "hello"},
            headers={
                "Authorization": "Bearer secret",
                "Cookie": "session=secret",
                "Forwarded": "for=attacker",
                "X-Forwarded-For": "203.0.113.10",
                "X-Trace": "trace-1",
            },
            content=b"request-body",
        )

    assert response.status_code == 201
    assert response.content == b"proxied"
    assert response.headers["x-backend"] == "yes"
    assert "x-backend-hop" not in response.headers
    assert captured["url"] == "http://backend.local:8080/api/run?q=hello"
    assert captured["body"] == b"request-body"
    headers = captured["headers"]
    assert "authorization" not in headers
    assert "cookie" not in headers
    assert "forwarded" not in headers
    assert headers["x-trace"] == "trace-1"
    assert headers["x-endpoint-route"] == "sandbox-1-8080"
    assert headers["x-forwarded-for"] == "testclient"
    assert runtime.purposes == [EndpointPurpose.PROXY]


def test_ingress_rejects_oversized_request_before_proxying() -> None:
    calls = 0

    async def backend(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    app = create_app(
        settings=ingress_settings(max_request_bytes=1024),
        runtime=IngressRuntime(),  # type: ignore[arg-type]
        ingress_transport=httpx.MockTransport(backend),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/sandboxes/sandbox-1/proxy/8080",
            content=b"x" * 1025,
        )

    assert response.status_code == 413
    assert calls == 0


def test_api_key_protects_ingress_but_not_health() -> None:
    settings = ingress_settings()
    settings.server = ServerSettings(api_key="a-secure-api-key")
    app = create_app(
        settings=settings,
        runtime=IngressRuntime(),  # type: ignore[arg-type]
    )

    with TestClient(app) as client:
        unauthorized = client.get(
            "/v1/sandboxes/sandbox-1/proxy/8080"
        )
        authorized_endpoint = client.get(
            "/v1/sandboxes/sandbox-1/endpoints/8080",
            headers={"AgentGuard-API-Key": "a-secure-api-key"},
        )
        health = client.get("/health")

    assert unauthorized.status_code == 401
    assert authorized_endpoint.status_code == 200
    assert health.status_code == 200


def test_websocket_ingress_relays_text_frames(monkeypatch: Any) -> None:
    class Backend:
        subprotocol = "agentguard.test"

        def __init__(self) -> None:
            self.sent: list[str | bytes] = []
            self.delivered = False

        async def send(self, payload: str | bytes) -> None:
            self.sent.append(payload)

        async def recv(self) -> str:
            if not self.delivered:
                self.delivered = True
                return "backend-ready"
            await asyncio.Future()
            raise AssertionError("unreachable")

        async def close(self, code: int = 1000) -> None:
            del code

    backend = Backend()
    calls: list[tuple[str, dict[str, Any]]] = []

    def connect(url: str, **kwargs: Any) -> Any:
        calls.append((url, kwargs))

        class Context:
            async def __aenter__(self) -> Backend:
                return backend

            async def __aexit__(self, *args: Any) -> None:
                return None

        return Context()

    monkeypatch.setattr("agentguard.ingress.proxy.websockets.connect", connect)
    app = create_app(
        settings=ingress_settings(),
        runtime=IngressRuntime(),  # type: ignore[arg-type]
    )

    with TestClient(app) as client:
        with client.websocket_connect(
            "/v1/sandboxes/sandbox-1/proxy/8080/ws",
            subprotocols=["agentguard.test"],
        ) as websocket:
            websocket.send_text("client-message")
            assert websocket.receive_text() == "backend-ready"

    assert calls[0][0] == "ws://backend.local:8080/ws"
    assert calls[0][1]["subprotocols"] == ["agentguard.test"]
    assert backend.sent == ["client-message"]
