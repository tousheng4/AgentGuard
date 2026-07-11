from pydantic import BaseModel, Field

class SandboxRunRequest(BaseModel):
    argv: list[str] = Field(min_length=1)
    timeout_seconds:int = Field(default=10, ge=1, le=30)

class SandboxRunResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str