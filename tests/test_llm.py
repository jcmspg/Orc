from types import SimpleNamespace

import pytest

from orc.llm import DEFAULT_MODEL, complete


class FakeMessages:
    def __init__(self, response):
        self._response = response
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._response


def fake_client(response):
    messages = FakeMessages(response)
    return SimpleNamespace(messages=messages), messages


def text_response(*texts, stop_reason="end_turn"):
    content = [SimpleNamespace(type="thinking", thinking="")]
    content += [SimpleNamespace(type="text", text=t) for t in texts]
    return SimpleNamespace(content=content, stop_reason=stop_reason, stop_details=None)


def test_returns_concatenated_text_blocks():
    client, _ = fake_client(text_response("Hello", ", world"))
    assert complete(client, "hi") == "Hello, world"


def test_sends_default_model_adaptive_thinking_and_effort():
    client, messages = fake_client(text_response("ok"))
    complete(client, "hi", system="be brief")

    kwargs = messages.last_kwargs
    assert kwargs["model"] == DEFAULT_MODEL
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["output_config"] == {"effort": "high"}
    assert kwargs["system"] == "be brief"
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]


def test_omits_system_when_not_given():
    client, messages = fake_client(text_response("ok"))
    complete(client, "hi")
    assert "system" not in messages.last_kwargs


def test_refusal_raises():
    response = SimpleNamespace(
        content=[],
        stop_reason="refusal",
        stop_details=SimpleNamespace(category="cyber"),
    )
    client, _ = fake_client(response)
    with pytest.raises(RuntimeError, match="cyber"):
        complete(client, "hi")
