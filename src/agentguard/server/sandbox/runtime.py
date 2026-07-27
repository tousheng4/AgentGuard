from __future__ import annotations

import io
import tarfile
import time
from pathlib import Path
from typing import Any

import agentguard

RUNTIME_DIR = "/opt/agentguard-runtime"
BOOTSTRAP_PATH = f"{RUNTIME_DIR}/bootstrap.sh"

BOOTSTRAP_SCRIPT = """#!/bin/sh
set -u

PYTHON_BIN="$(command -v python3 || command -v python || true)"
if [ -z "$PYTHON_BIN" ]; then
    echo "AgentGuard requires python3 or python in the sandbox image" >&2
    exit 127
fi

PYTHONPATH=/opt "$PYTHON_BIN" -m agentguard.execd.server &
EXECD_PID=$!

"$@" &
CHILD_PID=$!

forward_signal() {
    kill -TERM "$CHILD_PID" 2>/dev/null || true
    kill -TERM "$EXECD_PID" 2>/dev/null || true
}

trap forward_signal TERM INT HUP

wait "$CHILD_PID"
STATUS=$?
kill -TERM "$EXECD_PID" 2>/dev/null || true
wait "$EXECD_PID" 2>/dev/null || true
exit "$STATUS"
"""


class DockerRuntimeInjector:
    def __init__(self) -> None:
        package_file = agentguard.__file__
        if package_file is None:
            raise RuntimeError("Unable to locate the agentguard package")
        self._package_dir = Path(package_file).resolve().parent
        self._archive: bytes | None = None

    def inject(self, container: Any) -> None:
        container.put_archive(path="/", data=self._build_archive())

    def _build_archive(self) -> bytes:
        if self._archive is not None:
            return self._archive

        stream = io.BytesIO()
        now = int(time.time())
        with tarfile.open(fileobj=stream, mode="w") as archive:
            self._add_directory(archive, "opt", now, uid=0, gid=0)
            self._add_directory(archive, "opt/agentguard", now, uid=0, gid=0)
            self._add_directory(archive, "opt/agentguard-runtime", now, uid=0, gid=0)
            self._add_directory(archive, "workspace", now, uid=10001, gid=10001)

            script = BOOTSTRAP_SCRIPT.encode("utf-8")
            script_info = tarfile.TarInfo("opt/agentguard-runtime/bootstrap.sh")
            script_info.size = len(script)
            script_info.mode = 0o755
            script_info.mtime = now
            archive.addfile(script_info, io.BytesIO(script))

            for path in sorted(self._package_dir.rglob("*")):
                relative = path.relative_to(self._package_dir)
                if "__pycache__" in relative.parts or path.suffix == ".pyc":
                    continue
                target = Path("opt/agentguard") / relative
                if path.is_dir():
                    self._add_directory(archive, str(target), now, uid=0, gid=0)
                    continue
                info = archive.gettarinfo(str(path), arcname=str(target))
                info.uid = 0
                info.gid = 0
                info.uname = "root"
                info.gname = "root"
                with path.open("rb") as source:
                    archive.addfile(info, source)

        self._archive = stream.getvalue()
        return self._archive

    @staticmethod
    def _add_directory(
        archive: tarfile.TarFile,
        name: str,
        mtime: int,
        *,
        uid: int,
        gid: int,
    ) -> None:
        info = tarfile.TarInfo(name)
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        info.mtime = mtime
        info.uid = uid
        info.gid = gid
        archive.addfile(info)
