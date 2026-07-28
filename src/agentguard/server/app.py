from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

import uvicorn
from fastapi import FastAPI, Request

from agentguard.config import AppSettings, get_settings
from agentguard.server.api.debug import router as debug_router
from agentguard.server.api.lifecycle import router as lifecycle_router
from agentguard.server.api.tools import router as tools_router
from agentguard.server.sandbox.factory import create_sandbox_runtime
from agentguard.server.sandbox.service import SandboxRuntime


def create_app(
    settings: AppSettings | None = None,
    runtime: SandboxRuntime | None = None,
) -> FastAPI:
    active_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        active_runtime = runtime or create_sandbox_runtime(active_settings)
        application.state.sandbox_runtime = active_runtime
        application.state.settings = active_settings
        try:
            yield
        finally:
            active_runtime.close()

    application = FastAPI(title="AgentGuard", version="0.1.0", lifespan=lifespan)
    application.state.settings = active_settings
    application.include_router(lifecycle_router)
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
