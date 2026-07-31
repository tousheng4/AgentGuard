from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request, WebSocket
from fastapi.responses import StreamingResponse

from agentguard.config import AppSettings
from agentguard.ingress.proxy import proxy_http_request, proxy_websocket_request
from agentguard.server.api.lifecycle import get_lifecycle_service
from agentguard.server.sandbox.service import SandboxRuntime

router = APIRouter(prefix="/v1", tags=["sandbox-ingress"])


def get_app_settings(request: Request) -> AppSettings:
    return cast(AppSettings, request.app.state.settings)


@router.api_route(
    "/sandboxes/{sandbox_id}/proxy/{port}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
@router.api_route(
    "/sandboxes/{sandbox_id}/proxy/{port}/{full_path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy_sandbox_http(
    request: Request,
    sandbox_id: str,
    port: int,
    runtime: Annotated[SandboxRuntime, Depends(get_lifecycle_service)],
    settings: Annotated[AppSettings, Depends(get_app_settings)],
    full_path: str = "",
) -> StreamingResponse:
    return await proxy_http_request(
        request,
        runtime,
        settings.ingress,
        sandbox_id,
        port,
        full_path,
    )


@router.websocket("/sandboxes/{sandbox_id}/proxy/{port}")
@router.websocket("/sandboxes/{sandbox_id}/proxy/{port}/{full_path:path}")
async def proxy_sandbox_websocket(
    websocket: WebSocket,
    sandbox_id: str,
    port: int,
    full_path: str = "",
) -> None:
    runtime = websocket.app.state.sandbox_runtime
    settings = websocket.app.state.settings
    await proxy_websocket_request(
        websocket,
        runtime,
        settings,
        sandbox_id,
        port,
        full_path,
    )
