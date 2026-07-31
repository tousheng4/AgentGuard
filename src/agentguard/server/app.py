import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

import httpx
import uvicorn
from fastapi import FastAPI, Request

from agentguard.config import AppSettings, get_settings
from agentguard.server.api.debug import router as debug_router
from agentguard.server.api.ingress import router as ingress_router
from agentguard.server.api.lifecycle import router as lifecycle_router
from agentguard.server.api.tools import router as tools_router
from agentguard.server.auth import APIKeyMiddleware
from agentguard.server.sandbox.factory import create_sandbox_runtime
from agentguard.server.sandbox.service import SandboxRuntime

logger = logging.getLogger(__name__)


def create_app(
    settings: AppSettings | None = None,
    runtime: SandboxRuntime | None = None,
    ingress_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    active_settings = settings or get_settings()
    if active_settings.ingress.enabled and active_settings.server.api_key is None:
        logger.warning(
            "Ingress is enabled without an API key; only use this configuration "
            "on a trusted network"
        )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        active_runtime = runtime or create_sandbox_runtime(active_settings)
        application.state.sandbox_runtime = active_runtime
        application.state.settings = active_settings
        application.state.ingress_http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=active_settings.ingress.connect_timeout_seconds,
                read=active_settings.ingress.idle_timeout_seconds,
                write=active_settings.ingress.idle_timeout_seconds,
                pool=active_settings.ingress.connect_timeout_seconds,
            ),
            transport=ingress_transport,
            trust_env=False,
        )
        try:
            yield
        finally:
            await application.state.ingress_http_client.aclose()
            active_runtime.close()

    application = FastAPI(title="AgentGuard", version="0.1.0", lifespan=lifespan)
    application.add_middleware(APIKeyMiddleware, settings=active_settings)
    application.state.settings = active_settings
    application.include_router(lifecycle_router)
    application.include_router(ingress_router)
    application.include_router(debug_router)
    application.include_router(tools_router)

    @application.get("/health")
    async def health(request: Request) -> dict[str, str]:
        active_runtime = cast(
            SandboxRuntime | None,
            getattr(request.app.state, "sandbox_runtime", None),
        )
        return {
            "status": "ok",
            "runtime": (
                active_runtime.name
                if active_runtime is not None
                else active_settings.runtime.type
            ),
        }

    return application


app = create_app()


def run() -> None:
    uvicorn.run(
        "agentguard.server.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
