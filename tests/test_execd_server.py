from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from agentguard.execd.filesystem import FilesystemService
from agentguard.execd.server import ExecdHandler


@pytest.fixture
def execd_url(tmp_path: Path) -> Iterator[str]:
    class TestExecdHandler(ExecdHandler):
        filesystem = FilesystemService(tmp_path)

    server = ThreadingHTTPServer(("127.0.0.1", 0), TestExecdHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_write_read_and_list_files(execd_url: str, tmp_path: Path) -> None:
    target = tmp_path / "src" / "main.py"

    with httpx.Client(base_url=execd_url) as client:
        write_response = client.post(
            "/files/write",
            json={"path": str(target), "content": "print('你好')\n"},
        )
        read_response = client.get("/files/read", params={"path": str(target)})
        list_response = client.get(
            "/directories/list",
            params={"path": str(target.parent)},
        )

    assert write_response.status_code == 200
    assert write_response.json()["bytes_written"] == len("print('你好')\n".encode())
    assert read_response.json() == {"content": "print('你好')\n"}
    assert list_response.status_code == 200
    assert list_response.json()["entries"][0]["name"] == "main.py"
    assert list_response.json()["entries"][0]["type"] == "file"


def test_upload_and_download_binary_file(execd_url: str, tmp_path: Path) -> None:
    target = tmp_path / "assets" / "payload.bin"
    content = b"\x00\xffAgentGuard\x00"
    files = [
        (
            "metadata",
            ("metadata", json.dumps({"path": str(target)}), "application/json"),
        ),
        ("file", ("payload.bin", content, "application/octet-stream")),
    ]

    with httpx.Client(base_url=execd_url) as client:
        upload_response = client.post("/files/upload", files=files)
        download_response = client.get(
            "/files/download",
            params={"path": str(target)},
        )

    assert upload_response.status_code == 200
    assert download_response.status_code == 200
    assert download_response.content == content
    assert download_response.headers["content-type"] == "application/octet-stream"


def test_file_routes_reject_path_outside_workspace(
    execd_url: str,
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside.txt"

    with httpx.Client(base_url=execd_url) as client:
        response = client.post(
            "/files/write",
            json={"path": str(outside), "content": "blocked"},
        )

    assert response.status_code == 403
    assert not outside.exists()


def test_file_routes_reject_relative_paths(execd_url: str) -> None:
    with httpx.Client(base_url=execd_url) as client:
        response = client.get("/files/read", params={"path": "relative.txt"})

    assert response.status_code == 400


def test_file_routes_reject_symlink_escape(
    execd_url: str,
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside-directory"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "escape"
    link.symlink_to(outside, target_is_directory=True)

    with httpx.Client(base_url=execd_url) as client:
        response = client.post(
            "/files/write",
            json={"path": str(link / "file.txt"), "content": "blocked"},
        )

    assert response.status_code == 403
    assert not (outside / "file.txt").exists()
