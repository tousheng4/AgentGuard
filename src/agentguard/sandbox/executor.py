import logging
import os
from typing import Any

from docker.errors import (  # type: ignore[import-not-found]
    APIError,
    DockerException,
    ImageNotFound,
    NotFound,
)
from requests.exceptions import ReadTimeout

import docker
from agentguard.sandbox.models import SandboxRunResult

logger = logging.getLogger(__name__)


class SandboxExecutionError(RuntimeError):
    """Sandbox 创建或执行失败。"""


class SandboxTimeoutError(SandboxExecutionError):
    """Sandbox 执行超时。"""


class SandboxExecutor:
    def __init__(self, image: str | None = None) -> None:
        self._image = image or os.environ.get(
            "AGENTGUARD_SANDBOX_IMAGE",
            "agentguard-sandbox:latest",
        )
        self._client: Any = docker.from_env()  # type: ignore[attr-defined]

    def ping(self) -> bool:
        return bool(self._client.ping())

    def run(self, argv: list[str], timeout_seconds: int) -> SandboxRunResult:
        if not argv:
            raise ValueError("argv must not be empty")

        container = None

        try:
            container = self._client.containers.create(
                image=self._image,
                command=argv,
                working_dir="/app/workspaces",

                # 进程身份
                user="10001:10001",

                # 默认禁止网络
                network_disabled=True,

                # 容器根文件系统只读
                read_only=True,

                # 移除 Linux capabilities
                cap_drop=["ALL"],

                # 禁止进程获取额外权限
                security_opt=["no-new-privileges:true"],

                # 资源限制
                mem_limit="256m",
                nano_cpus=500_000_000,
                pids_limit=64,

                # 给程序提供有限的临时写入空间
                tmpfs={
                    "/tmp": "rw,noexec,nosuid,size=64m",
                },

                stdin_open=False,
                tty=False,

                labels={
                    "agentguard.managed": "true",
                },
            )

            container.start()

            wait_result = container.wait(timeout=timeout_seconds)

            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")

            return SandboxRunResult(
                exit_code=wait_result["StatusCode"],
                stdout=stdout,
                stderr=stderr,
            )

        except ReadTimeout as e:
            if container is not None:
                container.kill()
            raise SandboxTimeoutError(
                f"Sandbox execution timed out after {timeout_seconds} seconds"
            ) from e

        except ImageNotFound as e:
            raise SandboxExecutionError(f"Sandbox image '{self._image}' not found") from e

        except (APIError, DockerException) as e:
            raise SandboxExecutionError(f"Failed to execute sandbox: {e}") from e

        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except NotFound:
                    pass
                except DockerException:
                    logger.exception(
                        "Failed to remove sandbox container: %s",
                        container.id,
                    )
