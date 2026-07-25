import json

import httpx
import pytest

from agentguard.sdk.client import AgentGuardClient


@pytest.mark.asyncio
async def test_client_create_run_kill_flow() -> None:
    deleted: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/sandboxes":
            return httpx.Response(
                201,
                json={
                    "id": "sandbox-1",
                    "image": "agentguard-sandbox:latest",
                    "state": "running",
                },
            )
        if (
            request.method == "GET"
            and request.url.path == "/v1/sandboxes/sandbox-1/endpoints/44772"
        ):
            return httpx.Response(200, json={"endpoint": "execd.local:44772"})
        if request.method == "POST" and str(request.url) == "http://execd.local:44772/command":
            body = json.loads(request.content)
            assert body["command"] == "echo hi"
            return httpx.Response(200, json={"exit_code": 0, "stdout": "hi\n", "stderr": ""})
        if request.method == "DELETE" and request.url.path == "/v1/sandboxes/sandbox-1":
            deleted.append("sandbox-1")
            return httpx.Response(204)
        return httpx.Response(404)

    client = AgentGuardClient(
        "http://agentguard.local",
        transport=httpx.MockTransport(handler),
    )

    sandbox = await client.create_sandbox()
    result = await sandbox.commands.run("echo hi")
    await sandbox.kill()

    assert sandbox.id == "sandbox-1"
    assert result.exit_code == 0
    assert result.stdout == "hi\n"
    assert deleted == ["sandbox-1"]
