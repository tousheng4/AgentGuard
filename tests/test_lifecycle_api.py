from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from agentguard.server.api.lifecycle import get_lifecycle_service
from agentguard.server.app import app
from agentguard.server.sandbox.models import (
    CreateSandboxRequest,
    RenewSandboxExpirationResponse,
    SandboxEndpoint,
    SandboxInfo,
    SandboxListResponse,
    SandboxState,
    SandboxStatus,
)


class FakeLifecycleService:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.paused: list[str] = []
        self.resumed: list[str] = []

    def create(self, request: CreateSandboxRequest) -> SandboxInfo:
        return self._info(
            id="sandbox-1",
            image=request.image or "agentguard-sandbox:latest",
            metadata=request.metadata,
            entrypoint=request.entrypoint,
        )

    def get(self, sandbox_id: str) -> SandboxInfo:
        return self._info(
            id=sandbox_id,
            image="agentguard-sandbox:latest",
        )

    def list_sandboxes(
        self,
        *,
        states: list[SandboxState] | None = None,
        metadata: dict[str, str] | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> SandboxListResponse:
        del states, metadata
        return SandboxListResponse(
            items=[self._info(id="sandbox-1", image="agentguard-sandbox:latest")],
            page=page,
            page_size=page_size,
            total_items=1,
            total_pages=1,
        )

    def delete(self, sandbox_id: str) -> None:
        self.deleted.append(sandbox_id)

    def pause(self, sandbox_id: str) -> None:
        self.paused.append(sandbox_id)

    def resume(self, sandbox_id: str) -> None:
        self.resumed.append(sandbox_id)

    def renew_expiration(
        self,
        sandbox_id: str,
        timeout_seconds: int,
    ) -> RenewSandboxExpirationResponse:
        del sandbox_id
        return RenewSandboxExpirationResponse(
            expires_at=datetime.now(UTC) + timedelta(seconds=timeout_seconds)
        )

    def endpoint(self, sandbox_id: str, port: int) -> SandboxEndpoint:
        return SandboxEndpoint(endpoint=f"127.0.0.1:{port}")

    @staticmethod
    def _info(
        *,
        id: str,
        image: str,
        metadata: dict[str, str] | None = None,
        entrypoint: list[str] | None = None,
    ) -> SandboxInfo:
        state = SandboxState.RUNNING
        return SandboxInfo(
            id=id,
            image=image,
            state=state,
            status=SandboxStatus(state=state),
            metadata=metadata or {},
            created_at=datetime(2026, 7, 27, tzinfo=UTC),
            entrypoint=entrypoint or ["tail", "-f", "/dev/null"],
            exposed_ports=[44772],
        )


def test_lifecycle_create_get_endpoint_delete() -> None:
    service = FakeLifecycleService()
    app.dependency_overrides[get_lifecycle_service] = lambda: service

    try:
        client = TestClient(app)

        create_response = client.post("/v1/sandboxes", json={"image": "python:3.11-slim"})
        assert create_response.status_code == 201
        create_payload = create_response.json()
        assert create_payload["id"] == "sandbox-1"
        assert create_payload["image"] == "python:3.11-slim"
        assert create_payload["state"] == "running"
        assert create_payload["status"]["state"] == "running"

        get_response = client.get("/v1/sandboxes/sandbox-1")
        assert get_response.status_code == 200
        assert get_response.json()["id"] == "sandbox-1"

        endpoint_response = client.get("/v1/sandboxes/sandbox-1/endpoints/44772")
        assert endpoint_response.status_code == 200
        assert endpoint_response.json() == {"endpoint": "127.0.0.1:44772"}

        list_response = client.get("/v1/sandboxes?page=1&page_size=10")
        assert list_response.status_code == 200
        assert list_response.json()["total_items"] == 1

        pause_response = client.post("/v1/sandboxes/sandbox-1/pause")
        resume_response = client.post("/v1/sandboxes/sandbox-1/resume")
        assert pause_response.status_code == 202
        assert resume_response.status_code == 202
        assert service.paused == ["sandbox-1"]
        assert service.resumed == ["sandbox-1"]

        renew_response = client.post(
            "/v1/sandboxes/sandbox-1/renew-expiration",
            json={"timeout_seconds": 3600},
        )
        assert renew_response.status_code == 200
        assert renew_response.json()["expires_at"]

        delete_response = client.delete("/v1/sandboxes/sandbox-1")
        assert delete_response.status_code == 204
        assert service.deleted == ["sandbox-1"]
    finally:
        app.dependency_overrides.clear()
