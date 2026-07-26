import json

import httpx
import pytest

from agentguard.sdk.client import AgentGuardClient


@pytest.mark.asyncio
async def test_client_waits_for_execd_readiness() -> None:
    ping_attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal ping_attempts
        if request.method == "POST" and request.url.path == "/v1/sandboxes":
            return httpx.Response(
                201,
                json={"id": "sandbox-1", "image": "image", "state": "running"},
            )
        if request.url.path == "/v1/sandboxes/sandbox-1/endpoints/44772":
            return httpx.Response(200, json={"endpoint": "execd.local:44772"})
        if request.url.path == "/ping":
            ping_attempts += 1
            if ping_attempts < 3:
                raise httpx.ConnectError("execd is starting", request=request)
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(404)

    client = AgentGuardClient(
        "http://agentguard.local",
        transport=httpx.MockTransport(handler),
    )
    sandbox = await client.create_sandbox()

    assert sandbox.id == "sandbox-1"
    assert ping_attempts == 3


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
        if request.method == "GET" and str(request.url) == "http://execd.local:44772/ping":
            return httpx.Response(200, json={"status": "ok"})
        if request.method == "POST" and str(request.url) == "http://execd.local:44772/command":
            body = json.loads(request.content)
            assert body["command"] == "echo hi"
            return httpx.Response(200, json={"exit_code": 0, "stdout": "hi\n", "stderr": ""})
        if (
            request.method == "POST"
            and request.url.path == "/files/write"
            and request.url.host == "execd.local"
        ):
            body = json.loads(request.content)
            assert body == {
                "path": "/workspace/main.py",
                "content": "print(1 + 1)",
                "encoding": "utf-8",
            }
            return httpx.Response(200, json={"path": body["path"], "bytes_written": 12})
        if (
            request.method == "GET"
            and request.url.path == "/files/read"
            and request.url.host == "execd.local"
        ):
            assert request.url.params["path"] == "/workspace/main.py"
            return httpx.Response(200, json={"content": "print(1 + 1)"})
        if request.method == "DELETE" and request.url.path == "/v1/sandboxes/sandbox-1":
            deleted.append("sandbox-1")
            return httpx.Response(204)
        return httpx.Response(404)

    client = AgentGuardClient(
        "http://agentguard.local",
        transport=httpx.MockTransport(handler),
    )

    sandbox = await client.create_sandbox()
    await sandbox.files.write_file("/workspace/main.py", "print(1 + 1)")
    content = await sandbox.files.read_file("/workspace/main.py")
    result = await sandbox.commands.run("echo hi")
    await sandbox.kill()

    assert sandbox.id == "sandbox-1"
    assert result.exit_code == 0
    assert result.stdout == "hi\n"
    assert content == "print(1 + 1)"
    assert deleted == ["sandbox-1"]


@pytest.mark.asyncio
async def test_files_client_upload_download_and_list() -> None:
    uploaded = b"\x00binary\xff"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/files/upload":
            assert b"/workspace/input.bin" in request.content
            assert uploaded in request.content
            return httpx.Response(200, json={"files": []})
        if request.method == "GET" and request.url.path == "/files/download":
            return httpx.Response(200, content=uploaded)
        if request.method == "GET" and request.url.path == "/directories/list":
            return httpx.Response(
                200,
                json={
                    "entries": [
                        {
                            "path": "/workspace/input.bin",
                            "name": "input.bin",
                            "type": "file",
                            "size": len(uploaded),
                            "modified_at": "2026-07-26T00:00:00+00:00",
                        }
                    ]
                },
            )
        return httpx.Response(404)

    from agentguard.sdk.files import FilesClient

    files = FilesClient(
        endpoint="execd.local:44772",
        transport=httpx.MockTransport(handler),
    )
    await files.upload_file("/workspace/input.bin", uploaded)
    downloaded = await files.download_file("/workspace/input.bin")
    entries = await files.list_directory("/workspace")

    assert downloaded == uploaded
    assert entries[0].name == "input.bin"
