"""Issue #3245: the document gate did not exclude the SKUs it says it does.

``_supports_document_blocks`` documents two exclusions:

    Claude 3.5 Sonnet+ and the Claude 4.x Sonnet/Opus families support
    documents. Haiku -- including Haiku 4.5 -- does not. Older Claude 3 SKUs
    are likewise excluded; we keep the gate conservative so a regression here
    surfaces as a graceful skip rather than a 400 from the API.

It implemented only the first::

    if "haiku" in m:
        return False
    return True

so ``claude-3-opus-20240229`` and ``claude-3-sonnet-20240229`` -- which predate
native ``document`` blocks -- were told they supported them. A PDF then went to
the API as a document block and came back 400, instead of taking the text
fallback sitting a few lines below in the same function.

The matcher is the interesting half: Claude 3.5 and 3.7 spell themselves
``claude-3-5-sonnet`` / ``claude-3-7-sonnet``, so excluding on ``"claude-3"``
would turn this into the opposite bug.
"""

from __future__ import annotations

import pytest

from agentos.provider.anthropic import _build_message_payload, _supports_document_blocks
from agentos.provider.types import ContentBlockDocument, Message

_LEGACY = [
    "claude-3-opus-20240229",
    "claude-3-sonnet-20240229",
    # Vendor-prefixed spellings of the same SKUs.
    "anthropic.claude-3-sonnet-20240229-v1:0",
    "claude-3-sonnet@20240229",
    "claude-3-opus-latest",
]

_SUPPORTED = [
    "claude-3-5-sonnet-20241022",
    "claude-3-7-sonnet-20250219",
    "claude-sonnet-4-20250514",
    "claude-opus-4-1",
    "claude-opus-5",
    "claude-sonnet-5",
]


@pytest.mark.parametrize("model", _LEGACY)
def test_a_pre_document_claude_3_sku_is_excluded(model: str) -> None:
    """The issue's repro, and the docstring's own second exclusion."""
    assert _supports_document_blocks(model) is False


@pytest.mark.parametrize("model", _SUPPORTED)
def test_the_supported_families_are_unaffected(model: str) -> None:
    """The line the fix must not cross. ``claude-3-5-sonnet`` contains
    ``claude-3``; matching on that prefix would strip documents from every
    model that actually supports them."""
    assert _supports_document_blocks(model) is True


@pytest.mark.parametrize(
    "model",
    ["claude-3-haiku-20240307", "claude-haiku-4-5-20251001"],
)
def test_haiku_is_still_excluded(model: str) -> None:
    assert _supports_document_blocks(model) is False


def _document_message() -> Message:
    return Message(
        role="user",
        content=[
            ContentBlockDocument(
                source_type="base64",
                media_type="application/pdf",
                data="JVBERi0xLjQK",
                title="report.pdf",
            )
        ],
    )


def test_a_legacy_sku_takes_the_text_fallback_instead_of_a_400() -> None:
    """End-to-end through the payload builder: the outcome that matters is
    that no document block is sent, because that block is what the API
    rejects."""
    payload = _build_message_payload(_document_message(), model="claude-3-opus-20240229")

    parts = payload["content"]
    assert all(part.get("type") != "document" for part in parts), parts
    fallback = next(
        part
        for part in parts
        if part.get("type") == "text"
        and "[document attached but not consumable by this model]" in part.get("text", "")
    )
    assert "report.pdf" in fallback["text"]


def test_a_supported_sku_still_sends_the_document_block() -> None:
    payload = _build_message_payload(_document_message(), model="claude-3-5-sonnet-20241022")

    parts = payload["content"]
    document = next(part for part in parts if part.get("type") == "document")
    assert document["source"]["media_type"] == "application/pdf"


def test_an_unknown_model_is_still_allowed() -> None:
    """The gate names what it knows is broken rather than allow-listing, so a
    model this code has never heard of is not silently downgraded."""
    assert _supports_document_blocks("some-future-claude") is True
