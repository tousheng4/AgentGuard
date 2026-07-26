from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

CommandEvent = dict[str, Any]
_StreamItem = tuple[str, str | None]


class StreamingCommand:
    def __init__(
        self,
        command: str,
        *,
        cwd: str,
        timeout_seconds: int,
        ping_interval_seconds: float = 3.0,
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.timeout_seconds = timeout_seconds
        self.ping_interval_seconds = ping_interval_seconds
        self.execution_id = str(uuid4())
        self._process: subprocess.Popen[bytes] | None = None

    def events(self) -> Iterator[CommandEvent]:
        started_at = time.monotonic()
        output: queue.Queue[_StreamItem] = queue.Queue()
        readers: list[threading.Thread] = []
        timed_out = False

        try:
            self._process = subprocess.Popen(
                ["sh", "-c", self.command],
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            yield self._event("init", text=self.execution_id)

            assert self._process.stdout is not None
            assert self._process.stderr is not None
            readers = [
                self._start_reader("stdout", self._process.stdout, output),
                self._start_reader("stderr", self._process.stderr, output),
            ]

            closed_streams = 0
            next_ping = time.monotonic() + self.ping_interval_seconds
            while closed_streams < 2 or self._process.poll() is None:
                now = time.monotonic()
                elapsed = now - started_at
                if elapsed >= self.timeout_seconds and self._process.poll() is None:
                    timed_out = True
                    self.terminate()

                wait_seconds = max(0.0, min(0.1, next_ping - now))
                try:
                    stream, text = output.get(timeout=wait_seconds)
                except queue.Empty:
                    if time.monotonic() >= next_ping:
                        yield self._event("ping", text="pong")
                        next_ping = time.monotonic() + self.ping_interval_seconds
                    continue

                if text is None:
                    closed_streams += 1
                    continue
                yield self._event(stream, text=text)

            for reader in readers:
                reader.join(timeout=1)
            exit_code = self._process.wait()
            execution_time_ms = int((time.monotonic() - started_at) * 1000)

            if timed_out:
                message = f"Command timed out after {self.timeout_seconds} seconds"
                yield self._event(
                    "error",
                    error={
                        "ename": "CommandTimeoutError",
                        "evalue": message,
                        "traceback": [message],
                    },
                )
                return

            if exit_code != 0:
                yield self._event(
                    "error",
                    error={
                        "ename": "CommandExecError",
                        "evalue": str(exit_code),
                        "traceback": [f"Command exited with code {exit_code}"],
                    },
                )
                return

            yield self._event(
                "execution_complete",
                execution_time=execution_time_ms,
            )
        except OSError as exc:
            yield self._event(
                "error",
                error={
                    "ename": "CommandExecError",
                    "evalue": str(exc),
                    "traceback": [str(exc)],
                },
            )
        finally:
            self.terminate()
            for reader in readers:
                reader.join(timeout=1)

    def terminate(self) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return

        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=0.5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            if process.poll() is None:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait()

    def _start_reader(
        self,
        stream_name: str,
        pipe: Any,
        output: queue.Queue[_StreamItem],
    ) -> threading.Thread:
        def read() -> None:
            try:
                while chunk := pipe.readline():
                    output.put((stream_name, chunk.decode("utf-8", errors="replace")))
            finally:
                pipe.close()
                output.put((stream_name, None))

        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        return thread

    @staticmethod
    def _event(event_type: str, **payload: Any) -> CommandEvent:
        return {
            "type": event_type,
            **payload,
            "timestamp": int(time.time() * 1000),
        }
