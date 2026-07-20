from typing import Literal

from pydantic import BaseModel, Field

from agentguard.policy.models import PolicyDecision
from agentguard.sandbox.models import SandboxRunResult


class ShellExecAction(BaseModel):
    argv: list[str] = Field(min_length=1)
    cwd: str = "/workspace"
    timeout_seconds: int = Field(default=10, ge=1, le=30)


class ShellExecResponse(BaseModel):
    status: Literal["executed", "denied", "requires_approval"]
    decision: PolicyDecision
    result: SandboxRunResult | None = None
