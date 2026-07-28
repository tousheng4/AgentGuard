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


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENTGUARD_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    runtime: RuntimeSettings = RuntimeSettings()
    docker: DockerRuntimeSettings = DockerRuntimeSettings()


def get_settings() -> AppSettings:
    return AppSettings()
