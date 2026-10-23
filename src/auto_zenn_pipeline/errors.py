"""Pipeline exceptions and hierarchy."""

from collections.abc import Sequence


class PipelineError(Exception):
    pass


class ConfigError(PipelineError):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"invalid config: key={key}")


class SessionError(PipelineError):
    def __init__(self, session: str, file: str | None) -> None:
        self.session = session
        self.file = file
        super().__init__(f"invalid session: session={session}, file={file}")


class SecretDetectedError(PipelineError):
    def __init__(
        self,
        session: str,
        file: str,
        location: str,
        pattern_name: str,
    ) -> None:
        self.session = session
        self.file = file
        self.location = location
        self.pattern_name = pattern_name
        super().__init__(
            f"secret detected: session={session}, file={file}, location={location}, pattern={pattern_name}"
        )


class LogTooLargeError(PipelineError):
    def __init__(self, session: str, size: int, limit: int) -> None:
        self.session = session
        self.size = size
        self.limit = limit
        super().__init__(
            f"log too large: session={session}, size={size}, limit={limit}"
        )


class LlmOutputError(PipelineError):
    def __init__(self, session: str, step: str) -> None:
        self.session = session
        self.step = step
        super().__init__(f"invalid LLM output: session={session}, step={step}")


class AlreadyGeneratedError(PipelineError):
    def __init__(self, session: str, ref: str) -> None:
        self.session = session
        self.ref = ref
        super().__init__(f"already generated: session={session}, ref={ref}")


class PublishError(PipelineError):
    def __init__(self, session: str, command: str) -> None:
        self.session = session
        self.command = command
        super().__init__(f"publish failed: session={session}, command={command}")


class GateBlockedError(PipelineError):
    def __init__(self, target: str, reasons: Sequence[str]) -> None:
        self.target = target
        self.reasons = tuple(reasons)
        super().__init__(
            f"gate blocked: target={target}, reasons={','.join(self.reasons)}"
        )


class BundleError(PipelineError):
    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"invalid bundle: path={path}, reason={reason}")
