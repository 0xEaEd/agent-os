"""``OllamaProvider`` must send ``ChatConfig.stop_sequences`` to the model.

OpenAI maps them to ``payload["stop"]`` and Anthropic to
``payload["stop_sequences"]``; Ollama takes them under ``options.stop``. The Ollama
provider built its payload without them, so a turn that relied on a stop boundary ran
past it and the model carried on inventing tool results or a user turn (#3033).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from agentos.provider import ChatConfig, Message
from agentos.provider.ollama import OllamaProvider

DONE = (
    '{"model":"llama3:latest","message":{"role":"assistant","content":"hi"},"done":false}\n'
    '{"model":"llama3:latest","message":{"role":"assistant","content":""},'
    '"done":true,"done_reason":"stop","prompt_eval_count":3,"eval_count":1}\n'
)


def _capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, text=DONE)

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def patched_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr("agentos.provider.ollama.httpx.AsyncClient", patched_async_client)
    return captured


def _run(config: ChatConfig) -> None:
    provider = OllamaProvider(model="llama3:latest")

    messages = [Message(role="user", content="q")]

    async def _drain() -> list[Any]:
        return [event async for event in provider.chat(messages, config=config)]

    asyncio.run(_drain())


def test_stop_sequences_are_sent_as_ollama_options_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch)

    _run(ChatConfig(stop_sequences=["Observation:", "\nUser:"]))

    assert captured["payload"]["options"]["stop"] == ["Observation:", "\nUser:"]


def test_other_options_survive_alongside_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch)

    _run(ChatConfig(stop_sequences=["STOP"], temperature=0.2, max_tokens=64))

    options = captured["payload"]["options"]
    assert options["stop"] == ["STOP"]
    assert options["temperature"] == 0.2
    assert options["num_predict"] == 64


def test_no_stop_key_is_sent_when_none_are_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch)

    _run(ChatConfig())

    assert "stop" not in captured["payload"]["options"]
