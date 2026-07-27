from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class SandboxRunRequest(BaseModel):
    argv: list[str] = Field(min_length=1)
    timeout_seconds: int = Field(default=10, ge=1, le=30)


class SandboxRunResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str


class SandboxState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    FAILED = "failed"


class SandboxStatus(BaseModel):
    state: SandboxState
    reason: str | None = None
    message: str | None = None
    last_transition_at: datetime | None = None


class SandboxResourceLimits(BaseModel):
    cpu: float = Field(default=1.0, gt=0)
    memory_mb: int = Field(default=512, ge=64)
    pids: int = Field(default=128, ge=16)


class CreateSandboxRequest(BaseModel):
    image: str | None = None
    timeout_seconds: int | None = Field(default=1800, ge=1, le=24 * 60 * 60)
    entrypoint: list[str] = Field(
        default_factory=lambda: ["tail", "-f", "/dev/null"],
        min_length=1,
    )
    env: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, str] = Field(default_factory=dict)
    resource_limits: SandboxResourceLimits = Field(
        default_factory=SandboxResourceLimits,
    )
    exposed_ports: list[int] = Field(default_factory=list)

    @field_validator("entrypoint")
    @classmethod
    def validate_entrypoint(cls, value: list[str]) -> list[str]:
        if any(not item for item in value):
            raise ValueError("entrypoint items must be non-empty strings")
        return value

    @field_validator("exposed_ports")
    @classmethod
    def validate_ports(cls, value: list[int]) -> list[int]:
        if any(port < 1 or port > 65535 for port in value):
            raise ValueError("exposed ports must be between 1 and 65535")
        return sorted(set(value))


class SandboxInfo(BaseModel):
    id: str
    image: str
    state: SandboxState
    status: SandboxStatus
    metadata: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
    expires_at: datetime | None = None
    entrypoint: list[str] = Field(default_factory=list)
    resource_limits: SandboxResourceLimits = Field(
        default_factory=SandboxResourceLimits,
    )
    exposed_ports: list[int] = Field(default_factory=list)


class SandboxEndpoint(BaseModel):
    endpoint: str


class SandboxListResponse(BaseModel):
    items: list[SandboxInfo]
    page: int
    page_size: int
    total_items: int
    total_pages: int


class RenewSandboxExpirationRequest(BaseModel):
    timeout_seconds: int = Field(ge=1, le=24 * 60 * 60)


class RenewSandboxExpirationResponse(BaseModel):
    expires_at: datetime


class ExecdCommandRequest(BaseModel):
    command: str = Field(min_length=1)
    cwd: str = "/workspace"
    timeout_seconds: int = Field(default=30, ge=1, le=300)
