from collections.abc import Callable

from agentguard.config import AppSettings
from agentguard.server.sandbox.docker import DockerSandboxRuntime
from agentguard.server.sandbox.service import SandboxRuntime

RuntimeBuilder = Callable[[AppSettings], SandboxRuntime]


def _build_docker_runtime(settings: AppSettings) -> SandboxRuntime:
    return DockerSandboxRuntime(
        default_image=settings.docker.image,
        data_dir=settings.docker.data_dir,
        execd_ready_timeout_seconds=settings.docker.execd_ready_timeout_seconds,
        bind_host=settings.docker.bind_host,
    )


_RUNTIME_REGISTRY: dict[str, RuntimeBuilder] = {
    "docker": _build_docker_runtime,
}


def create_sandbox_runtime(settings: AppSettings) -> SandboxRuntime:
    runtime_type = settings.runtime.type.lower()
    try:
        builder = _RUNTIME_REGISTRY[runtime_type]
    except KeyError as exc:
        available = ", ".join(sorted(_RUNTIME_REGISTRY))
        raise ValueError(
            f"Unsupported sandbox runtime '{runtime_type}'. Available runtimes: {available}"
        ) from exc
    return builder(settings)


def list_available_runtimes() -> list[str]:
    return sorted(_RUNTIME_REGISTRY)
