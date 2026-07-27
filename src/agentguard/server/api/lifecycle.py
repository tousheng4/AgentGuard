from typing import Annotated, Protocol
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from agentguard.server.sandbox.docker import (
    DockerSandboxLifecycle,
    SandboxEndpointUnavailableError,
    SandboxLifecycleError,
    SandboxNotFoundError,
    SandboxStateConflictError,
)
from agentguard.server.sandbox.models import (
    CreateSandboxRequest,
    RenewSandboxExpirationRequest,
    RenewSandboxExpirationResponse,
    SandboxEndpoint,
    SandboxInfo,
    SandboxListResponse,
    SandboxState,
)


class SandboxLifecycleProtocol(Protocol):
    def create(self, request: CreateSandboxRequest) -> SandboxInfo:
        pass

    def get(self, sandbox_id: str) -> SandboxInfo:
        pass

    def list_sandboxes(
        self,
        *,
        states: list[SandboxState] | None = None,
        metadata: dict[str, str] | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> SandboxListResponse:
        pass

    def delete(self, sandbox_id: str) -> None:
        pass

    def pause(self, sandbox_id: str) -> None:
        pass

    def resume(self, sandbox_id: str) -> None:
        pass

    def renew_expiration(
        self,
        sandbox_id: str,
        timeout_seconds: int,
    ) -> RenewSandboxExpirationResponse:
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


@router.get("/sandboxes", response_model=SandboxListResponse)
def list_sandboxes(
    service: Annotated[SandboxLifecycleProtocol, Depends(get_lifecycle_service)],
    state: Annotated[list[SandboxState] | None, Query()] = None,
    metadata: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
) -> SandboxListResponse:
    metadata_filter: dict[str, str] | None = None
    if metadata:
        try:
            metadata_filter = dict(
                parse_qsl(metadata, keep_blank_values=True, strict_parsing=True)
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid metadata filter: {exc}",
            ) from exc
    try:
        return service.list_sandboxes(
            states=state,
            metadata=metadata_filter,
            page=page,
            page_size=page_size,
        )
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


@router.post("/sandboxes/{sandbox_id}/pause", status_code=status.HTTP_202_ACCEPTED)
def pause_sandbox(
    sandbox_id: str,
    service: Annotated[SandboxLifecycleProtocol, Depends(get_lifecycle_service)],
) -> Response:
    try:
        service.pause(sandbox_id)
        return Response(status_code=status.HTTP_202_ACCEPTED)
    except SandboxNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SandboxStateConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SandboxLifecycleError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/sandboxes/{sandbox_id}/resume", status_code=status.HTTP_202_ACCEPTED)
def resume_sandbox(
    sandbox_id: str,
    service: Annotated[SandboxLifecycleProtocol, Depends(get_lifecycle_service)],
) -> Response:
    try:
        service.resume(sandbox_id)
        return Response(status_code=status.HTTP_202_ACCEPTED)
    except SandboxNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SandboxStateConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SandboxLifecycleError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/sandboxes/{sandbox_id}/renew-expiration",
    response_model=RenewSandboxExpirationResponse,
)
def renew_sandbox_expiration(
    sandbox_id: str,
    request: RenewSandboxExpirationRequest,
    service: Annotated[SandboxLifecycleProtocol, Depends(get_lifecycle_service)],
) -> RenewSandboxExpirationResponse:
    try:
        return service.renew_expiration(sandbox_id, request.timeout_seconds)
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
    if port < 1 or port > 65535:
        raise HTTPException(status_code=400, detail="port must be between 1 and 65535")
    try:
        return service.endpoint(sandbox_id, port)
    except SandboxNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SandboxEndpointUnavailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SandboxLifecycleError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
