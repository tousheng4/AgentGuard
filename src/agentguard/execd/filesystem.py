from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


class FilesystemError(ValueError):
    pass


class PathOutsideWorkspaceError(FilesystemError):
    pass


@dataclass(frozen=True)
class DirectoryEntry:
    path: str
    name: str
    type: str
    size: int
    modified_at: str

    def as_dict(self) -> dict[str, str | int]:
        return {
            "path": self.path,
            "name": self.name,
            "type": self.type,
            "size": self.size,
            "modified_at": self.modified_at,
        }


class FilesystemService:
    def __init__(self, workspace_root: str | Path = "/workspace") -> None:
        self.workspace_root = Path(workspace_root).resolve()

    def resolve_path(self, raw_path: str) -> Path:
        if not raw_path:
            raise FilesystemError("path must be a non-empty string")

        candidate = Path(raw_path)
        if not candidate.is_absolute():
            raise FilesystemError("path must be absolute")

        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self.workspace_root):
            raise PathOutsideWorkspaceError(
                f"path must be inside {self.workspace_root}"
            )
        return resolved

    def write_bytes(self, raw_path: str, content: bytes) -> int:
        path = self.resolve_path(raw_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.is_dir():
            raise IsADirectoryError(raw_path)
        path.write_bytes(content)
        return len(content)

    def read_bytes(self, raw_path: str) -> bytes:
        path = self.resolve_path(raw_path)
        if path.is_dir():
            raise IsADirectoryError(raw_path)
        return path.read_bytes()

    def file_for_download(self, raw_path: str) -> tuple[Path, int]:
        path = self.resolve_path(raw_path)
        if path.is_dir():
            raise IsADirectoryError(raw_path)
        return path, path.stat().st_size

    def list_directory(self, raw_path: str) -> list[DirectoryEntry]:
        path = self.resolve_path(raw_path)
        if not path.exists():
            raise FileNotFoundError(raw_path)
        if not path.is_dir():
            raise NotADirectoryError(raw_path)

        entries: list[DirectoryEntry] = []
        for child in sorted(path.iterdir(), key=lambda item: item.name):
            stat = child.lstat()
            if child.is_symlink():
                entry_type = "symlink"
            elif child.is_dir():
                entry_type = "directory"
            else:
                entry_type = "file"
            entries.append(
                DirectoryEntry(
                    path=str(child),
                    name=child.name,
                    type=entry_type,
                    size=stat.st_size,
                    modified_at=datetime.fromtimestamp(
                        stat.st_mtime,
                        tz=UTC,
                    ).isoformat(),
                )
            )
        return entries


def workspace_root_from_env() -> Path:
    return Path(os.environ.get("AGENTGUARD_WORKSPACE_ROOT", "/workspace"))
