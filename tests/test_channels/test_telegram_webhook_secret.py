"""Regression tests for issue #962.

``_handle_webhook`` compared the inbound ``X-Telegram-Bot-Api-Secret-Token``
header with ``!=``, which short-circuits on the first differing byte. The
resulting latency difference lets a remote caller recover the secret one byte
at a time. The comparison must be constant-time, matching what
``gateway/auth.py`` and ``channels/slack.py`` already do.
"""

from __future__ import annotations

import hmac
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from agentos.channels.telegram import TelegramChannel, TelegramChannelConfig

SECRET = "s3cr3t-webhook-token"


def _client(channel: TelegramChannel) -> TestClient:
    app = Starlette(routes=[channel.create_webhook_route()])
    return TestClient(app)


def _channel() -> TelegramChannel:
    return TelegramChannel(
        TelegramChannelConfig(token="token", mode="webhook", webhook_secret_token=SECRET)
    )


def _update() -> dict[str, Any]:
    return {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "date": 1_700_000_000,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "is_bot": False, "first_name": "Ada"},
            "text": "hello",
        },
    }


def test_matching_secret_is_accepted() -> None:
    with _client(_channel()) as client:
        resp = client.post(
            "/telegram/events",
            json=_update(),
            headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
        )
    assert resp.status_code == 200


@pytest.mark.parametrize(
    "candidate",
    [
        pytest.param("", id="empty"),
        pytest.param("x", id="single-byte"),
        pytest.param(SECRET[:-1], id="matching-prefix"),
        pytest.param(SECRET + "extra", id="matching-prefix-then-longer"),
        pytest.param(SECRET.upper(), id="wrong-case"),
    ],
)
def test_wrong_secret_is_rejected(candidate: str) -> None:
    with _client(_channel()) as client:
        resp = client.post(
            "/telegram/events",
            json=_update(),
            headers={"X-Telegram-Bot-Api-Secret-Token": candidate},
        )
    assert resp.status_code == 401


def test_missing_header_is_rejected() -> None:
    with _client(_channel()) as client:
        resp = client.post("/telegram/events", json=_update())
    assert resp.status_code == 401


def test_verification_goes_through_compare_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    """The attacker-controlled side must never reach a short-circuiting ``!=``.

    Timing safety is not observable from a response, so this asserts the
    mechanism: the handler must route the header through
    ``hmac.compare_digest``. Patching the attribute on the shared ``hmac``
    module (rather than a dotted path into ``telegram``) means a handler that
    went back to ``!=`` records no call and fails on the assertion below.
    """
    calls: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a: Any, b: Any) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)

    with _client(_channel()) as client:
        resp = client.post(
            "/telegram/events",
            json=_update(),
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )

    assert resp.status_code == 401
    assert (b"wrong", SECRET.encode("utf-8")) in calls


def test_rejected_update_is_not_enqueued() -> None:
    channel = _channel()
    with _client(channel) as client:
        client.post(
            "/telegram/events",
            json=_update(),
            headers={"X-Telegram-Bot-Api-Secret-Token": SECRET[:-1] + "!"},
        )
    assert channel._queue.empty()  # noqa: SLF001
