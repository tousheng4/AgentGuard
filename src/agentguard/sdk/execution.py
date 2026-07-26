from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OutputMessage(BaseModel):
    text: str
    timestamp: int
    is_error: bool = False


class ExecutionError(BaseModel):
    name: str
    value: str
    timestamp: int
    traceback: list[str] = Field(default_factory=list)


class ExecutionComplete(BaseModel):
    timestamp: int
    execution_time_in_millis: int


class ExecutionLogs(BaseModel):
    stdout: list[OutputMessage] = Field(default_factory=list)
    stderr: list[OutputMessage] = Field(default_factory=list)


class Execution(BaseModel):
    id: str | None = None
    exit_code: int | None = None
    logs: ExecutionLogs = Field(default_factory=ExecutionLogs)
    error: ExecutionError | None = None
    complete: ExecutionComplete | None = None

    @property
    def text(self) -> str:
        return "".join(message.text for message in self.logs.stdout)


AsyncEventHandler = Callable[[Any], Awaitable[None]]


class ExecutionHandlers(BaseModel):
    on_stdout: AsyncEventHandler | None = None
    on_stderr: AsyncEventHandler | None = None
    on_init: AsyncEventHandler | None = None
    on_error: AsyncEventHandler | None = None
    on_execution_complete: AsyncEventHandler | None = None
    skip_accumulation: bool = False

    model_config = ConfigDict(arbitrary_types_allowed=True)
