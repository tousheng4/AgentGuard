from typing import Annotated, cast
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from agentguard.server.sandbox.errors import (
    SandboxEndpointUnavailableError,
    SandboxNotFoundError,
    SandboxRuntimeError,
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
from agentguard.server.sandbox.service import SandboxRuntime

router = APIRouter(prefix="/v1", tags=["sandbox-lifecycle"])


def get_lifecycle_service(request: Request) -> SandboxRuntime:
    runtime = getattr(request.app.state, "sandbox_runtime", None)
    if runtime is None:
        raise RuntimeError("Sandbox runtime has not been initialized")
    return cast(SandboxRuntime, runtime)


@router.post("/sandboxes", response_model=SandboxInfo, status_code=status.HTTP_201_CREATED)
def create_sandbox(
    request: CreateSandboxRequest,
    service: Annotated[SandboxRuntime, Depends(get_lifecycle_service)],
) -> SandboxInfo:
    try:
        return service.create(request)
    except SandboxRuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/sandboxes", response_model=SandboxListResponse)
def list_sandboxes(
    service: Annotated[SandboxRuntime, Depends(get_lifecycle_service)],
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
    except SandboxRuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/sandboxes/{sandbox_id}", response_model=SandboxInfo)
def get_sandbox(
    sandbox_id: str,
    service: Annotated[SandboxRuntime, Depends(get_lifecycle_service)],
) -> SandboxInfo:
    try:
        return service.get(sandbox_id)
    except SandboxNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SandboxRuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/sandboxes/{sandbox_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sandbox(
    sandbox_id: str,
    service: Annotated[SandboxRuntime, Depends(get_lifecycle_service)],
) -> Response:
    try:
        service.delete(sandbox_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except SandboxNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SandboxRuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/sandboxes/{sandbox_id}/pause", status_code=status.HTTP_202_ACCEPTED)
def pause_sandbox(
    sandbox_id: str,
    service: Annotated[SandboxRuntime, Depends(get_lifecycle_service)],
) -> Response:
    try:
        service.pause(sandbox_id)
        return Response(status_code=status.HTTP_202_ACCEPTED)
    except SandboxNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SandboxStateConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SandboxRuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/sandboxes/{sandbox_id}/resume", status_code=status.HTTP_202_ACCEPTED)
def resume_sandbox(
    sandbox_id: str,
    service: Annotated[SandboxRuntime, Depends(get_lifecycle_service)],
) -> Response:
    try:
        service.resume(sandbox_id)
        return Response(status_code=status.HTTP_202_ACCEPTED)
    except SandboxNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SandboxStateConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SandboxRuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/sandboxes/{sandbox_id}/renew-expiration",
    response_model=RenewSandboxExpirationResponse,
)
def renew_sandbox_expiration(
    sandbox_id: str,
    request: RenewSandboxExpirationRequest,
    service: Annotated[SandboxRuntime, Depends(get_lifecycle_service)],
) -> RenewSandboxExpirationResponse:
    try:
        return service.renew_expiration(sandbox_id, request.timeout_seconds)
    except SandboxNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SandboxRuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/sandboxes/{sandbox_id}/endpoints/{port}",
    response_model=SandboxEndpoint,
    response_model_exclude_none=True,
)
def get_sandbox_endpoint(
    request: Request,
    sandbox_id: str,
    port: int,
    service: Annotated[SandboxRuntime, Depends(get_lifecycle_service)],
    use_server_proxy: bool = False,
) -> SandboxEndpoint:
    if port < 1 or port > 65535:
        raise HTTPException(status_code=400, detail="port must be between 1 and 65535")
    try:
        endpoint = service.endpoint(sandbox_id, port)
        if not use_server_proxy:
            return endpoint
        settings = request.app.state.settings
        if not settings.ingress.enabled:
            raise HTTPException(status_code=400, detail="ingress is not enabled")
        address = settings.ingress.public_address or request.url.netloc
        root_path = request.scope.get("root_path", "").rstrip("/")
        return SandboxEndpoint(
            endpoint=(
                f"{address}{root_path}/v1/sandboxes/"
                f"{sandbox_id}/proxy/{port}"
            )
        )
    except SandboxNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SandboxEndpointUnavailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SandboxRuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
