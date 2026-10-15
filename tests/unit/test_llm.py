from types import SimpleNamespace
from typing import Any, cast

import anthropic  # type: ignore[import-not-found,unused-ignore]
import pytest

from auto_zenn_pipeline.errors import LlmOutputError
from auto_zenn_pipeline.llm import AnthropicLlmClient, LlmClient


class _FakeMessages:
    def __init__(self, response: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response: Any = response

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.response


class _FakeClient:
    def __init__(self, response: Any) -> None:
        self.messages = _FakeMessages(response)


def _make(fake: Any) -> AnthropicLlmClient:
    return AnthropicLlmClient(client=cast(anthropic.Anthropic, fake))


def _call(client: AnthropicLlmClient) -> dict[str, Any]:
    return client.call_tool(
        session="raw_logs/s1",
        step="adr",
        model="claude-sonnet-5-5",
        system="SYS",
        user="USER",
        tool_name="record_adr",
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        max_tokens=1234,
    )


def test_import() -> None:
    # AC-1
    assert LlmClient is not None
    assert AnthropicLlmClient is not None


def test_create_called_once() -> None:
    # AC-2
    response = SimpleNamespace(
        stop_reason="tool_use",
        content=[SimpleNamespace(type="tool_use", name="record_adr", input={"x": "a"})],
    )
    fake = _FakeClient(response)
    client = _make(fake)
    _call(client)
    assert len(fake.messages.calls) == 1


def test_create_kwargs() -> None:
    # AC-3
    response = SimpleNamespace(
        stop_reason="tool_use",
        content=[SimpleNamespace(type="tool_use", name="record_adr", input={"x": "a"})],
    )
    fake = _FakeClient(response)
    client = _make(fake)
    _call(client)
    call = fake.messages.calls[0]
    assert call["model"] == "claude-sonnet-5-5"
    assert call["max_tokens"] == 1234
    assert call["system"] == "SYS"
    assert call["messages"] == [{"role": "user", "content": "USER"}]


def test_tools_kwarg() -> None:
    # AC-4
    response = SimpleNamespace(
        stop_reason="tool_use",
        content=[SimpleNamespace(type="tool_use", name="record_adr", input={"x": "a"})],
    )
    fake = _FakeClient(response)
    client = _make(fake)
    _call(client)
    call = fake.messages.calls[0]
    tools = call["tools"]
    assert isinstance(tools, list)
    assert len(tools) == 1
    assert tools[0]["name"] == "record_adr"
    assert tools[0]["input_schema"] == {
        "type": "object",
        "properties": {"x": {"type": "string"}},
    }
    assert isinstance(tools[0]["description"], str)


def test_tool_choice_kwarg() -> None:
    # AC-5
    response = SimpleNamespace(
        stop_reason="tool_use",
        content=[SimpleNamespace(type="tool_use", name="record_adr", input={"x": "a"})],
    )
    fake = _FakeClient(response)
    client = _make(fake)
    _call(client)
    call = fake.messages.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": "record_adr"}


def test_returns_tool_input() -> None:
    # AC-6
    response = SimpleNamespace(
        stop_reason="tool_use",
        content=[SimpleNamespace(type="tool_use", name="record_adr", input={"x": "a"})],
    )
    fake = _FakeClient(response)
    client = _make(fake)
    res = _call(client)
    assert res == {"x": "a"}


def test_text_block_before_tool_use() -> None:
    # AC-7
    response = SimpleNamespace(
        stop_reason="tool_use",
        content=[
            SimpleNamespace(type="text", text="thinking..."),
            SimpleNamespace(type="tool_use", name="record_adr", input={"x": "a"}),
        ],
    )
    fake = _FakeClient(response)
    client = _make(fake)
    res = _call(client)
    assert res == {"x": "a"}


def test_first_matching_block_wins() -> None:
    # AC-8
    response = SimpleNamespace(
        stop_reason="tool_use",
        content=[
            SimpleNamespace(type="tool_use", name="record_adr", input={"x": "a"}),
            SimpleNamespace(type="tool_use", name="record_adr", input={"x": "b"}),
        ],
    )
    fake = _FakeClient(response)
    client = _make(fake)
    res = _call(client)
    assert res == {"x": "a"}


def test_max_tokens_raises() -> None:
    # AC-9, AC-13
    response = SimpleNamespace(
        stop_reason="max_tokens",
        content=[SimpleNamespace(type="tool_use", name="record_adr", input={"x": "a"})],
    )
    fake = _FakeClient(response)
    client = _make(fake)
    with pytest.raises(LlmOutputError) as excinfo:
        _call(client)
    assert excinfo.value.session == "raw_logs/s1"
    assert excinfo.value.step == "adr"
    assert len(fake.messages.calls) == 1


@pytest.mark.parametrize(
    "content",
    [
        [SimpleNamespace(type="text", text="hello")],
        [],
    ],
)
def test_no_tool_use_raises(content: list[Any]) -> None:
    # AC-10, AC-13
    response = SimpleNamespace(stop_reason="end_turn", content=content)
    fake = _FakeClient(response)
    client = _make(fake)
    with pytest.raises(LlmOutputError) as excinfo:
        _call(client)
    assert excinfo.value.session == "raw_logs/s1"
    assert excinfo.value.step == "adr"
    assert len(fake.messages.calls) == 1


def test_name_mismatch_raises() -> None:
    # AC-11, AC-13
    response = SimpleNamespace(
        stop_reason="tool_use",
        content=[SimpleNamespace(type="tool_use", name="other", input={"x": "a"})],
    )
    fake = _FakeClient(response)
    client = _make(fake)
    with pytest.raises(LlmOutputError) as excinfo:
        _call(client)
    assert excinfo.value.session == "raw_logs/s1"
    assert excinfo.value.step == "adr"
    assert len(fake.messages.calls) == 1


@pytest.mark.parametrize(
    "tool_input",
    [
        "str",
        ["a"],
        None,
    ],
)
def test_non_dict_input_raises(tool_input: Any) -> None:
    # AC-12, AC-13
    response = SimpleNamespace(
        stop_reason="tool_use",
        content=[SimpleNamespace(type="tool_use", name="record_adr", input=tool_input)],
    )
    fake = _FakeClient(response)
    client = _make(fake)
    with pytest.raises(LlmOutputError) as excinfo:
        _call(client)
    assert excinfo.value.session == "raw_logs/s1"
    assert excinfo.value.step == "adr"
    assert len(fake.messages.calls) == 1


def test_real_anthropic_forbidden() -> None:
    # AC-14
    with pytest.raises(RuntimeError):
        anthropic.Anthropic(api_key="dummy")


def test_default_constructor_forbidden() -> None:
    # AC-15
    with pytest.raises(RuntimeError):
        AnthropicLlmClient()


def test_protocol_assignable() -> None:
    # AC-16
    response = SimpleNamespace(
        stop_reason="tool_use",
        content=[SimpleNamespace(type="tool_use", name="record_adr", input={"x": "a"})],
    )
    fake = _FakeClient(response)
    x: LlmClient = AnthropicLlmClient(client=cast(anthropic.Anthropic, fake))
    res = x.call_tool(
        session="raw_logs/s1",
        step="adr",
        model="claude-sonnet-5-5",
        system="SYS",
        user="USER",
        tool_name="record_adr",
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        max_tokens=1234,
    )
    assert res == {"x": "a"}
