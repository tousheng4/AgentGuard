"""Compatibility imports for the former execd payload module.

Runtime backend contracts live in :mod:`agentguard.server.sandbox.service`.
New code should import the Docker payload injector from
:mod:`agentguard.server.sandbox.injector`.
"""

from agentguard.server.sandbox.injector import (
    BOOTSTRAP_PATH,
    BOOTSTRAP_SCRIPT,
    RUNTIME_DIR,
    DockerRuntimeInjector,
)

__all__ = [
    "BOOTSTRAP_PATH",
    "BOOTSTRAP_SCRIPT",
    "RUNTIME_DIR",
    "DockerRuntimeInjector",
]
