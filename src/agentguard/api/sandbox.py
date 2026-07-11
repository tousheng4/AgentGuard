from fastapi import APIRouter, HTTPException

from agentguard.sandbox.executor import SandboxExecutor, SandboxExecutionError, SandboxTimeoutError
from agentguard.sandbox.models import SandboxRunRequest, SandboxRunResult

router = APIRouter(prefix="/debug/sandbox", tags=["sandbox-debug"])

executor = SandboxExecutor()


@router.post("/run", response_model=SandboxRunResult)
def run_in_sandbox(request: SandboxRunRequest) -> SandboxRunResult:
    try:
        result = executor.run(argv=request.argv, timeout_seconds=request.timeout_seconds)
        return result
    except SandboxTimeoutError as e:
        raise HTTPException(status_code=408, detail=str(e))
    except SandboxExecutionError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")