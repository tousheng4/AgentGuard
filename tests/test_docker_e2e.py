from __future__ import annotations

import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from agentguard.server.app import create_app
from agentguard.server.sandbox.docker import DockerSandboxRuntime

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENTGUARD_RUN_DOCKER_E2E") != "1",
    reason="set AGENTGUARD_RUN_DOCKER_E2E=1 to run against a real Docker daemon",
)


def test_real_docker_create_write_execute_and_delete() -> None:
    runtime = DockerSandboxRuntime()
    application = create_app(runtime=runtime)

    with TestClient(application) as client:
        create_response = client.post(
            "/v1/sandboxes",
            json={
                "image": "agentguard-sandbox:latest",
                "timeout_seconds": 300,
                "metadata": {"test": "docker-e2e"},
            },
        )
        assert create_response.status_code == 201, create_response.text
        sandbox_id = create_response.json()["id"]

        try:
            endpoint_response = client.get(
                f"/v1/sandboxes/{sandbox_id}/endpoints/44772"
            )
            assert endpoint_response.status_code == 200, endpoint_response.text
            endpoint = endpoint_response.json()["endpoint"]

            with httpx.Client(
                base_url=f"http://{endpoint}",
                timeout=10,
                trust_env=False,
            ) as execd:
                write_response = execd.post(
                    "/files/write",
                    json={
                        "path": "/workspace/e2e.py",
                        "content": "print('agentguard-docker-e2e')\n",
                    },
                )
                assert write_response.status_code == 200, write_response.text

                events: list[dict[str, object]] = []
                with execd.stream(
                    "POST",
                    "/command",
                    json={
                        "command": "python /workspace/e2e.py",
                        "cwd": "/workspace",
                        "timeout_seconds": 10,
                    },
                ) as command_response:
                    assert command_response.status_code == 200
                    for line in command_response.iter_lines():
                        if line.startswith("data:"):
                            events.append(json.loads(line[5:].strip()))

            assert [event["type"] for event in events] == [
                "init",
                "stdout",
                "execution_complete",
            ]
            assert events[1]["text"] == "agentguard-docker-e2e\n"
        except Exception as exc:
            container = runtime._get_container(sandbox_id)  # type: ignore[attr-defined]
            container.reload()
            logs = container.logs(tail=100).decode("utf-8", errors="replace")
            pytest.fail(
                f"execd request failed: {exc}\n"
                f"container state: {container.attrs.get('State')}\n"
                f"container logs:\n{logs}"
            )
        finally:
            delete_response = client.delete(f"/v1/sandboxes/{sandbox_id}")
            assert delete_response.status_code == 204

        get_response = client.get(f"/v1/sandboxes/{sandbox_id}")
        assert get_response.status_code == 404
