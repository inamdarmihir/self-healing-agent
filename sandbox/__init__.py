"""Docker sandbox for safe execution of untrusted patches."""


class SandboxUnavailableError(RuntimeError):
    """Raised when the Docker daemon is unreachable or the sandbox image is missing."""
