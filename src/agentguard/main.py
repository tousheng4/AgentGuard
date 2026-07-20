import uvicorn
from fastapi import FastAPI

from agentguard.api.sandbox import router as sandbox_router
from agentguard.api.tools import router as tools_router

app = FastAPI(title="AgentGuard", version="0.1.0")

app.include_router(sandbox_router)
app.include_router(tools_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "runtime": "docker",
    }


def run() -> None:
    uvicorn.run(
        "agentguard.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    run()
