from agentguard.gateway.models import ShellExecAction, ShellExecResponse
from agentguard.policy.models import PolicyEffect
from agentguard.policy.shell import ShellPolicy
from agentguard.sandbox.executor import SandboxExecutor


class ShellGateway:
    def __init__(
        self,
        policy: ShellPolicy | None = None,
        executor: SandboxExecutor | None = None,
    ) -> None:
        self._policy = policy or ShellPolicy()
        self._executor = executor

    def execute(self, argv: list[str], cwd: str, timeout_seconds: int) -> ShellExecResponse:
        action = ShellExecAction(argv=argv, cwd=cwd, timeout_seconds=timeout_seconds)
        decision = self._policy.evaluate(action)

        if decision.effect is PolicyEffect.DENY:
            return ShellExecResponse(status="denied", decision=decision)

        if decision.effect is PolicyEffect.REQUIRE_APPROVAL:
            return ShellExecResponse(status="requires_approval", decision=decision)

        result = self._get_executor().run(
            argv=action.argv,
            timeout_seconds=action.timeout_seconds,
        )
        return ShellExecResponse(status="executed", decision=decision, result=result)

    def _get_executor(self) -> SandboxExecutor:
        if self._executor is None:
            self._executor = SandboxExecutor()
        return self._executor
