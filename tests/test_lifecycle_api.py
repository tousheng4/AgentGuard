from fastapi.testclient import TestClient

from agentguard.server.api.lifecycle import get_lifecycle_service
from agentguard.server.app import app
from agentguard.server.sandbox.models import (
    CreateSandboxRequest,
    SandboxEndpoint,
    SandboxInfo,
    SandboxState,
)


class FakeLifecycleService:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def create(self, request: CreateSandboxRequest) -> SandboxInfo:
        return SandboxInfo(
            id="sandbox-1",
            image=request.image or "agentguard-sandbox:latest",
            state=SandboxState.RUNNING,
        )

    def get(self, sandbox_id: str) -> SandboxInfo:
        return SandboxInfo(
            id=sandbox_id,
            image="agentguard-sandbox:latest",
            state=SandboxState.RUNNING,
        )

    def delete(self, sandbox_id: str) -> None:
        self.deleted.append(sandbox_id)

    def endpoint(self, sandbox_id: str, port: int) -> SandboxEndpoint:
        return SandboxEndpoint(endpoint=f"127.0.0.1:{port}")


def test_lifecycle_create_get_endpoint_delete() -> None:
    service = FakeLifecycleService()
    app.dependency_overrides[get_lifecycle_service] = lambda: service

    try:
        client = TestClient(app)

        create_response = client.post("/v1/sandboxes", json={"image": "python:3.11-slim"})
        assert create_response.status_code == 201
        assert create_response.json() == {
            "id": "sandbox-1",
            "image": "python:3.11-slim",
            "state": "running",
        }

        get_response = client.get("/v1/sandboxes/sandbox-1")
        assert get_response.status_code == 200
        assert get_response.json()["id"] == "sandbox-1"

        endpoint_response = client.get("/v1/sandboxes/sandbox-1/endpoints/44772")
        assert endpoint_response.status_code == 200
        assert endpoint_response.json() == {"endpoint": "127.0.0.1:44772"}

        delete_response = client.delete("/v1/sandboxes/sandbox-1")
        assert delete_response.status_code == 204
        assert service.deleted == ["sandbox-1"]
    finally:
        app.dependency_overrides.clear()
