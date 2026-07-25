from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, HTTPException, Response, status

from agentguard.server.sandbox.docker import (
    EXECD_PORT,
    DockerSandboxLifecycle,
    SandboxLifecycleError,
    SandboxNotFoundError,
)
from agentguard.server.sandbox.models import CreateSandboxRequest, SandboxEndpoint, SandboxInfo


class SandboxLifecycleProtocol(Protocol):
    def create(self, request: CreateSandboxRequest) -> SandboxInfo:
        pass

    def get(self, sandbox_id: str) -> SandboxInfo:
        pass

    def delete(self, sandbox_id: str) -> None:
        pass

    def endpoint(self, sandbox_id: str, port: int) -> SandboxEndpoint:
        pass


router = APIRouter(prefix="/v1", tags=["sandbox-lifecycle"])
_lifecycle_service: DockerSandboxLifecycle | None = None


def get_lifecycle_service() -> SandboxLifecycleProtocol:
    global _lifecycle_service
    if _lifecycle_service is None:
        _lifecycle_service = DockerSandboxLifecycle()
    return _lifecycle_service


@router.post("/sandboxes", response_model=SandboxInfo, status_code=status.HTTP_201_CREATED)
def create_sandbox(
    request: CreateSandboxRequest,
    service: Annotated[SandboxLifecycleProtocol, Depends(get_lifecycle_service)],
) -> SandboxInfo:
    try:
        return service.create(request)
    except SandboxLifecycleError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/sandboxes/{sandbox_id}", response_model=SandboxInfo)
def get_sandbox(
    sandbox_id: str,
    service: Annotated[SandboxLifecycleProtocol, Depends(get_lifecycle_service)],
) -> SandboxInfo:
    try:
        return service.get(sandbox_id)
    except SandboxNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SandboxLifecycleError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/sandboxes/{sandbox_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sandbox(
    sandbox_id: str,
    service: Annotated[SandboxLifecycleProtocol, Depends(get_lifecycle_service)],
) -> Response:
    try:
        service.delete(sandbox_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except SandboxNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SandboxLifecycleError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/sandboxes/{sandbox_id}/endpoints/{port}", response_model=SandboxEndpoint)
def get_sandbox_endpoint(
    sandbox_id: str,
    port: int,
    service: Annotated[SandboxLifecycleProtocol, Depends(get_lifecycle_service)],
) -> SandboxEndpoint:
    if port != EXECD_PORT:
        raise HTTPException(status_code=400, detail=f"Only port {EXECD_PORT} is supported")
    try:
        return service.endpoint(sandbox_id, port)
    except SandboxNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SandboxLifecycleError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
