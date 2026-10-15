import anthropic  # type: ignore[import-not-found]
import pytest


@pytest.fixture(autouse=True)
def _forbid_real_anthropic_client(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*args: object, **kwargs: object) -> None:
        raise RuntimeError("anthropic.Anthropic must not be instantiated in tests")

    monkeypatch.setattr(anthropic.Anthropic, "__init__", _raise)
