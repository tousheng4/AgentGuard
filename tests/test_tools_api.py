from fastapi.testclient import TestClient

from agentguard.api.tools import get_shell_gateway
from agentguard.gateway.models import ShellExecResponse
from agentguard.main import app
from agentguard.policy.models import PolicyDecision, PolicyEffect
from agentguard.sandbox.models import SandboxRunResult


class RecordingGateway:
    def __init__(self, response: ShellExecResponse) -> None:
        self.response = response
        self.argv: list[str] | None = None
        self.cwd: str | None = None
        self.timeout_seconds: int | None = None

    def execute(
        self,
        argv: list[str],
        cwd: str,
        timeout_seconds: int,
    ) -> ShellExecResponse:
        self.argv = argv
        self.cwd = cwd
        self.timeout_seconds = timeout_seconds
        return self.response


def test_shell_exec_endpoint_returns_gateway_response() -> None:
    gateway = RecordingGateway(
        ShellExecResponse(
            status="executed",
            decision=PolicyDecision(
                effect=PolicyEffect.ALLOW,
                risk_level="low",
                rule_id="shell.allowlisted_command",
                reason="Command is allowlisted for MVP execution.",
            ),
            result=SandboxRunResult(exit_code=0, stdout="hello\n", stderr=""),
        )
    )
    app.dependency_overrides[get_shell_gateway] = lambda: gateway

    try:
        response = TestClient(app).post(
            "/tools/shell/exec",
            json={"argv": ["echo", "hello"], "timeout_seconds": 3},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "status": "executed",
        "decision": {
            "effect": "ALLOW",
            "risk_level": "low",
            "rule_id": "shell.allowlisted_command",
            "reason": "Command is allowlisted for MVP execution.",
        },
        "result": {
            "exit_code": 0,
            "stdout": "hello\n",
            "stderr": "",
        },
    }
    assert gateway.argv == ["echo", "hello"]
    assert gateway.cwd == "/workspace"
    assert gateway.timeout_seconds == 3
