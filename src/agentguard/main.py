from fastapi import FastAPI
import uvicorn

from agentguard.api.sandbox import router as sandbox_router

app = FastAPI(title="AgentGuard", version="0.1.0")

app.include_router(sandbox_router)


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
