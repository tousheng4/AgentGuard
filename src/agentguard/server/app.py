from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from agentguard.server.api.debug import router as debug_router
from agentguard.server.api.lifecycle import (
    get_lifecycle_service,
)
from agentguard.server.api.lifecycle import (
    router as lifecycle_router,
)
from agentguard.server.api.tools import router as tools_router


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    del application
    get_lifecycle_service()
    yield


app = FastAPI(title="AgentGuard", version="0.1.0", lifespan=lifespan)

app.include_router(lifecycle_router)
app.include_router(debug_router)
app.include_router(tools_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "runtime": "docker",
    }


def run() -> None:
    uvicorn.run(
        "agentguard.server.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
