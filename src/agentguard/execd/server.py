from __future__ import annotations

import json
import subprocess
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class ExecdHandler(BaseHTTPRequestHandler):
    server_version = "AgentGuardExecd/0.1"

    def do_GET(self) -> None:
        if self.path == "/ping":
            self._write_json(HTTPStatus.OK, {"status": "ok"})
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path == "/command":
            self._handle_command()
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

    def _read_json(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length)
        if not body:
            return {}
        decoded = json.loads(body.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("JSON body must be an object")
        return decoded

    @staticmethod
    def _required_string(payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
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

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def run(host: str = "0.0.0.0", port: int = 44772) -> None:
    server = ThreadingHTTPServer((host, port), ExecdHandler)
    server.serve_forever()


if __name__ == "__main__":
    run()
