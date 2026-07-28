from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from agentguard.server.sandbox.executor import (
    SandboxExecutionError,
    SandboxTimeoutError,
)
from agentguard.server.sandbox.models import SandboxRunRequest, SandboxRunResult
from agentguard.server.sandbox.service import SandboxCommandRunner

router = APIRouter(prefix="/debug/sandbox", tags=["sandbox-debug"])


def get_command_runner(request: Request) -> SandboxCommandRunner:
    runtime = getattr(request.app.state, "sandbox_runtime", None)
    if not isinstance(runtime, SandboxCommandRunner):
        raise RuntimeError("Active sandbox runtime does not support command execution")
    return runtime


@router.post("/run", response_model=SandboxRunResult)
def run_in_sandbox(
    request: SandboxRunRequest,
    runner: Annotated[SandboxCommandRunner, Depends(get_command_runner)],
) -> SandboxRunResult:
    try:
        result = runner.run(
            argv=request.argv,
            timeout_seconds=request.timeout_seconds,
        )
        return result
    except SandboxTimeoutError as e:
        raise HTTPException(status_code=408, detail=str(e)) from e
    except SandboxExecutionError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}") from e
