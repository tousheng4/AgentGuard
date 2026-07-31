from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeSettings(BaseModel):
    type: str = Field(default="docker", min_length=1)


class DockerRuntimeSettings(BaseModel):
    image: str = "agentguard-sandbox:latest"
    data_dir: Path = Path("data")
    execd_ready_timeout_seconds: float = Field(default=5.0, gt=0)
    bind_host: str = "127.0.0.1"
    proxy_host: str = "127.0.0.1"


class IngressSettings(BaseModel):
    enabled: bool = False
    public_address: str | None = None
    connect_timeout_seconds: float = Field(default=5.0, gt=0)
    idle_timeout_seconds: float = Field(default=300.0, gt=0)
    max_request_bytes: int = Field(default=32 * 1024 * 1024, ge=1024)
    websocket_enabled: bool = True


class ServerSettings(BaseModel):
    api_key: str | None = Field(default=None, min_length=16)


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENTGUARD_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    runtime: RuntimeSettings = RuntimeSettings()
    docker: DockerRuntimeSettings = DockerRuntimeSettings()
    ingress: IngressSettings = IngressSettings()
    server: ServerSettings = ServerSettings()


def get_settings() -> AppSettings:
    return AppSettings()
