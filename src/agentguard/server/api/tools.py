from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request

from agentguard.gateway.models import ShellExecAction, ShellExecResponse
from agentguard.gateway.shell import ShellGateway
from agentguard.server.sandbox.executor import SandboxExecutionError, SandboxTimeoutError
from agentguard.server.sandbox.service import SandboxCommandRunner


class ShellGatewayProtocol(Protocol):
    def execute(self, argv: list[str], cwd: str, timeout_seconds: int) -> ShellExecResponse:
        pass


router = APIRouter(prefix="/tools", tags=["tools"])


def get_shell_gateway(request: Request) -> ShellGatewayProtocol:
    runtime = getattr(request.app.state, "sandbox_runtime", None)
    if not isinstance(runtime, SandboxCommandRunner):
        raise RuntimeError("Active sandbox runtime does not support command execution")
    return ShellGateway(executor=runtime)


@router.post("/shell/exec", response_model=ShellExecResponse)
def execute_shell(
    request: ShellExecAction,
    gateway: Annotated[ShellGatewayProtocol, Depends(get_shell_gateway)],
) -> ShellExecResponse:
    try:
        return gateway.execute(
            argv=request.argv,
            cwd=request.cwd,
            timeout_seconds=request.timeout_seconds,
        )
    except SandboxTimeoutError as e:
        raise HTTPException(status_code=408, detail=str(e)) from e
    except SandboxExecutionError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
