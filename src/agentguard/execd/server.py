from __future__ import annotations

import json
import subprocess
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from agentguard.execd.filesystem import (
    FilesystemError,
    FilesystemService,
    PathOutsideWorkspaceError,
    workspace_root_from_env,
)

MAX_REQUEST_BYTES = 32 * 1024 * 1024


class ExecdHandler(BaseHTTPRequestHandler):
    server_version = "AgentGuardExecd/0.1"
    filesystem = FilesystemService(workspace_root_from_env())

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/ping":
            self._write_json(HTTPStatus.OK, {"status": "ok"})
            return
        if path == "/files/read":
            self._handle_read_file()
            return
        if path == "/files/download":
            self._handle_download_file()
            return
        if path == "/directories/list":
            self._handle_list_directory()
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path == "/command":
            self._handle_command()
            return
        if path == "/files/write":
            self._handle_write_file()
            return
        if path == "/files/upload":
            self._handle_upload_file()
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _handle_command(self) -> None:
        try:
            payload = self._read_json()
            command = self._required_string(payload, "command")
            cwd = self._optional_string(payload, "cwd", "/workspace")
            timeout_seconds = self._optional_int(payload, "timeout_seconds", 30)
            completed = subprocess.run(
                ["sh", "-c", command],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
            self._write_json(
                HTTPStatus.OK,
                {
                    "exit_code": completed.returncode,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                },
            )
        except subprocess.TimeoutExpired as exc:
            self._write_json(
                HTTPStatus.REQUEST_TIMEOUT,
                {
                    "error": f"Command timed out after {exc.timeout} seconds",
                    "stdout": exc.stdout or "",
                    "stderr": exc.stderr or "",
                },
            )
        except Exception as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def _handle_write_file(self) -> None:
        try:
            payload = self._read_json()
            path = self._required_string(payload, "path")
            content = self._required_string(payload, "content", allow_empty=True)
            encoding = self._optional_string(payload, "encoding", "utf-8")
            written = self.filesystem.write_bytes(path, content.encode(encoding))
            self._write_json(HTTPStatus.OK, {"path": path, "bytes_written": written})
        except Exception as exc:
            self._write_filesystem_error(exc)

    def _handle_read_file(self) -> None:
        try:
            path = self._required_query("path")
            encoding = self._query().get("encoding", ["utf-8"])[0]
            content = self.filesystem.read_bytes(path).decode(encoding)
            self._write_json(HTTPStatus.OK, {"content": content})
        except Exception as exc:
            self._write_filesystem_error(exc)

    def _handle_upload_file(self) -> None:
        try:
            body = self._read_body()
            content_type = self.headers.get("Content-Type", "")
            if not content_type.startswith("multipart/form-data"):
                raise FilesystemError("Content-Type must be multipart/form-data")

            message = BytesParser(policy=default).parsebytes(
                f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
                + body
            )
            metadata_parts: list[dict[str, Any]] = []
            file_parts: list[bytes] = []
            for part in message.iter_parts():
                name = part.get_param("name", header="content-disposition")
                raw_content = part.get_payload(decode=True)
                if raw_content is None:
                    content = b""
                elif isinstance(raw_content, bytes):
                    content = raw_content
                else:
                    raise FilesystemError("multipart part must contain bytes")
                if name == "metadata":
                    metadata = json.loads(content.decode("utf-8"))
                    if not isinstance(metadata, dict):
                        raise FilesystemError("metadata must be a JSON object")
                    metadata_parts.append(metadata)
                elif name == "file":
                    file_parts.append(content)

            if not metadata_parts or not file_parts:
                raise FilesystemError("multipart body requires metadata and file parts")
            if len(metadata_parts) != len(file_parts):
                raise FilesystemError("metadata and file part counts must match")

            uploaded: list[dict[str, str | int]] = []
            for metadata, content in zip(metadata_parts, file_parts, strict=True):
                path = self._required_string(metadata, "path")
                written = self.filesystem.write_bytes(path, content)
                uploaded.append({"path": path, "bytes_written": written})
            self._write_json(HTTPStatus.OK, {"files": uploaded})
        except Exception as exc:
            self._write_filesystem_error(exc)

    def _handle_download_file(self) -> None:
        try:
            path, size = self.filesystem.file_for_download(self._required_query("path"))
            safe_filename = (
                path.name.replace('"', "_").replace("\r", "_").replace("\n", "_")
            )
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{safe_filename}"',
            )
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with path.open("rb") as source:
                while chunk := source.read(64 * 1024):
                    self.wfile.write(chunk)
        except Exception as exc:
            self._write_filesystem_error(exc)

    def _handle_list_directory(self) -> None:
        try:
            entries = self.filesystem.list_directory(self._required_query("path"))
            self._write_json(
                HTTPStatus.OK,
                {"entries": [entry.as_dict() for entry in entries]},
            )
        except Exception as exc:
            self._write_filesystem_error(exc)

    def _read_json(self) -> dict[str, Any]:
        body = self._read_body()
        if not body:
            return {}
        decoded = json.loads(body.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("JSON body must be an object")
        return decoded

    def _read_body(self) -> bytes:
        raw_content_length = self.headers.get("Content-Length")
        if raw_content_length is None:
            raise FilesystemError("Content-Length header is required")
        content_length = int(raw_content_length)
        if content_length < 0:
            raise FilesystemError("Content-Length must not be negative")
        if content_length > MAX_REQUEST_BYTES:
            raise RequestTooLargeError(f"request body exceeds {MAX_REQUEST_BYTES} bytes")
        return self.rfile.read(content_length)

    @staticmethod
    def _required_string(
        payload: dict[str, Any],
        key: str,
        *,
        allow_empty: bool = False,
    ) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or (not value and not allow_empty):
            raise ValueError(f"{key} must be a non-empty string")
        return value

    @staticmethod
    def _optional_string(payload: dict[str, Any], key: str, default: str) -> str:
        value = payload.get(key, default)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{key} must be a non-empty string")
        return value

    @staticmethod
    def _optional_int(payload: dict[str, Any], key: str, default: int) -> int:
        value = payload.get(key, default)
        if not isinstance(value, int) or value < 1:
            raise ValueError(f"{key} must be a positive integer")
        return value

    def _query(self) -> dict[str, list[str]]:
        return parse_qs(urlsplit(self.path).query, keep_blank_values=True)

    def _required_query(self, key: str) -> str:
        values = self._query().get(key)
        if not values or not values[0]:
            raise FilesystemError(f"missing query parameter '{key}'")
        return values[0]

    def _write_filesystem_error(self, exc: Exception) -> None:
        if isinstance(exc, RequestTooLargeError):
            status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        elif isinstance(exc, PathOutsideWorkspaceError):
            status = HTTPStatus.FORBIDDEN
        elif isinstance(exc, FileNotFoundError):
            status = HTTPStatus.NOT_FOUND
        elif isinstance(exc, (IsADirectoryError, NotADirectoryError)):
            status = HTTPStatus.CONFLICT
        elif isinstance(
            exc,
            (FilesystemError, UnicodeError, json.JSONDecodeError, ValueError),
        ):
            status = HTTPStatus.BAD_REQUEST
        else:
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        self._write_json(status, {"error": str(exc)})

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class RequestTooLargeError(FilesystemError):
    pass


def run(host: str = "0.0.0.0", port: int = 44772) -> None:
    server = ThreadingHTTPServer((host, port), ExecdHandler)
    server.serve_forever()


if __name__ == "__main__":
    run()
