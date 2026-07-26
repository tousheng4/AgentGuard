from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from io import BufferedIOBase
from typing import Any

import httpx
from pydantic import BaseModel


class FileType(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"


class DirectoryEntry(BaseModel):
    path: str
    name: str
    type: FileType
    size: int
    modified_at: str


@dataclass
class FilesClient:
    endpoint: str
    timeout_seconds: float = 30.0
    transport: httpx.AsyncBaseTransport | None = None

    async def write_file(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
    ) -> None:
        async with self._client() as client:
            response = await client.post(
                "/files/write",
                json={"path": path, "content": content, "encoding": encoding},
            )
            response.raise_for_status()

    async def read_file(
        self,
        path: str,
        *,
        encoding: str = "utf-8",
    ) -> str:
        async with self._client() as client:
            response = await client.get(
                "/files/read",
                params={"path": path, "encoding": encoding},
            )
            response.raise_for_status()
            content = response.json().get("content")
            if not isinstance(content, str):
                raise ValueError("execd returned an invalid file content response")
            return content

    async def upload_file(
        self,
        path: str,
        data: bytes | BufferedIOBase,
    ) -> None:
        metadata = json.dumps({"path": path})
        files: list[tuple[str, tuple[str, Any, str]]] = [
            ("metadata", ("metadata", metadata, "application/json")),
            ("file", (path.rsplit("/", 1)[-1] or "file", data, "application/octet-stream")),
        ]
        async with self._client() as client:
            response = await client.post("/files/upload", files=files)
            response.raise_for_status()

    async def download_file(self, path: str) -> bytes:
        async with self._client() as client:
            response = await client.get("/files/download", params={"path": path})
            response.raise_for_status()
            return response.content

    async def list_directory(self, path: str) -> list[DirectoryEntry]:
        async with self._client() as client:
            response = await client.get("/directories/list", params={"path": path})
            response.raise_for_status()
            payload = response.json()
            return [DirectoryEntry.model_validate(entry) for entry in payload["entries"]]

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"http://{self.endpoint}",
            timeout=self.timeout_seconds,
            transport=self.transport,
            trust_env=False,
        )
