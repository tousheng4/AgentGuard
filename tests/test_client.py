import json

import httpx
import pytest

from agentguard.sdk.client import AgentGuardClient
from agentguard.sdk.execution import ExecutionHandlers, OutputMessage


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
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=(
                    'data: {"type":"init","text":"execution-1","timestamp":1}\n\n'
                    'data: {"type":"stdout","text":"hi\\n","timestamp":2}\n\n'
                    'data: {"type":"execution_complete","execution_time":3,'
                    '"timestamp":4}\n\n'
                ),
            )
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
    assert result.text == "hi\n"
    assert result.id == "execution-1"
    assert content == "print(1 + 1)"
    assert deleted == ["sandbox-1"]


@pytest.mark.asyncio
async def test_commands_client_dispatches_streaming_handlers() -> None:
    messages: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=(
                'data: {"type":"init","text":"execution-1","timestamp":1}\n\n'
                'data: {"type":"stdout","text":"out\\n","timestamp":2}\n\n'
                'data: {"type":"stderr","text":"err\\n","timestamp":3}\n\n'
                'data: {"type":"execution_complete","execution_time":4,'
                '"timestamp":5}\n\n'
            ),
        )

    async def on_stdout(message: OutputMessage) -> None:
        messages.append(("stdout", message.text))

    async def on_stderr(message: OutputMessage) -> None:
        messages.append(("stderr", message.text))

    from agentguard.sdk.client import CommandsClient

    commands = CommandsClient(
        endpoint="execd.local:44772",
        transport=httpx.MockTransport(handler),
    )
    execution = await commands.run(
        "command",
        handlers=ExecutionHandlers(
            on_stdout=on_stdout,
            on_stderr=on_stderr,
        ),
    )

    assert messages == [("stdout", "out\n"), ("stderr", "err\n")]
    assert execution.text == "out\n"
    assert execution.logs.stderr[0].text == "err\n"
    assert execution.complete is not None


@pytest.mark.asyncio
async def test_commands_client_maps_nonzero_exit_event() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=(
                'data: {"type":"init","text":"execution-1","timestamp":1}\n\n'
                'data: {"type":"error","error":{"ename":"CommandExecError",'
                '"evalue":"7","traceback":[]},"timestamp":2}\n\n'
            ),
        )

    from agentguard.sdk.client import CommandsClient

    execution = await CommandsClient(
        endpoint="execd.local:44772",
        transport=httpx.MockTransport(handler),
    ).run("exit 7")

    assert execution.exit_code == 7
    assert execution.error is not None
    assert execution.error.name == "CommandExecError"


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
