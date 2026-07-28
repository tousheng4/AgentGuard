class SandboxRuntimeError(RuntimeError):
    """Base error raised by a sandbox runtime."""


class SandboxNotFoundError(SandboxRuntimeError):
    """The requested sandbox does not exist."""


class SandboxStateConflictError(SandboxRuntimeError):
    """The requested operation is invalid for the sandbox's current state."""


class SandboxEndpointUnavailableError(SandboxRuntimeError):
    """The requested sandbox endpoint cannot be resolved."""


class UnsupportedRuntimeOperationError(SandboxRuntimeError):
    """The active runtime does not support the requested operation."""


# Compatibility for callers using the old lifecycle-oriented name.
SandboxLifecycleError = SandboxRuntimeError
