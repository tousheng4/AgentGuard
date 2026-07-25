from enum import StrEnum

from pydantic import BaseModel, Field


class SandboxRunRequest(BaseModel):
    argv: list[str] = Field(min_length=1)
    timeout_seconds: int = Field(default=10, ge=1, le=30)


class SandboxRunResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str


class SandboxState(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"


class CreateSandboxRequest(BaseModel):
    image: str | None = None
    timeout_seconds: int = Field(default=1800, ge=1, le=24 * 60 * 60)


class SandboxInfo(BaseModel):
    id: str
    image: str
    state: SandboxState


class SandboxEndpoint(BaseModel):
    endpoint: str


class ExecdCommandRequest(BaseModel):
    command: str = Field(min_length=1)
    cwd: str = "/workspace"
    timeout_seconds: int = Field(default=30, ge=1, le=300)
