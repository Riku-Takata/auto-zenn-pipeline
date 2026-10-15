from typing import Any

import pytest

from auto_zenn_pipeline.errors import (
    AlreadyGeneratedError,
    ConfigError,
    LlmOutputError,
    LogTooLargeError,
    PipelineError,
    PublishError,
    SecretDetectedError,
    SessionError,
)

ERROR_CASES: list[tuple[type[PipelineError], dict[str, Any]]] = [
    (ConfigError, {"key": "models.adr"}),
    (SessionError, {"session": "raw_logs/s1", "file": "session.json"}),
    (
        SecretDetectedError,
        {
            "session": "raw_logs/s1",
            "file": "session.json",
            "location": "line 10",
            "pattern_name": "api_key",
        },
    ),
    (LogTooLargeError, {"session": "raw_logs/s1", "size": 300, "limit": 200}),
    (LlmOutputError, {"session": "raw_logs/s1", "step": "summarize"}),
    (AlreadyGeneratedError, {"session": "raw_logs/s1", "ref": "commit-123"}),
    (PublishError, {"session": "raw_logs/s1", "command": "zenn preview"}),
]


def test_pipeline_error_is_exception() -> None:
    assert issubclass(PipelineError, Exception)


@pytest.mark.parametrize("cls,kwargs", ERROR_CASES)
def test_subclass(cls: type[PipelineError], kwargs: dict[str, Any]) -> None:
    assert issubclass(cls, PipelineError)


@pytest.mark.parametrize("cls,kwargs", ERROR_CASES)
def test_keyword_attributes(cls: type[PipelineError], kwargs: dict[str, Any]) -> None:
    exc = cls(**kwargs)
    for attr, expected_val in kwargs.items():
        assert getattr(exc, attr) == expected_val


@pytest.mark.parametrize("cls,kwargs", ERROR_CASES)
def test_positional_attributes(cls: type[PipelineError], kwargs: dict[str, Any]) -> None:
    exc = cls(*kwargs.values())
    for attr, expected_val in kwargs.items():
        assert getattr(exc, attr) == expected_val


def test_session_error_file_none() -> None:
    exc = SessionError(session="raw_logs/s1", file=None)
    assert exc.session == "raw_logs/s1"
    assert exc.file is None


@pytest.mark.parametrize("cls,kwargs", ERROR_CASES)
def test_str_contains_identifier(cls: type[PipelineError], kwargs: dict[str, Any]) -> None:
    exc = cls(**kwargs)
    expected_id = kwargs["key"] if cls is ConfigError else kwargs["session"]
    assert expected_id in str(exc)


@pytest.mark.parametrize("cls,kwargs", ERROR_CASES)
def test_caught_as_pipeline_error(cls: type[PipelineError], kwargs: dict[str, Any]) -> None:
    with pytest.raises(PipelineError):
        raise cls(**kwargs)
