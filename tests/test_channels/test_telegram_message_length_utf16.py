"""Issue #3547: the 4096 cap was measured in code points, not UTF-16 units.

Telegram counts a message's text in UTF-16 code units -- the same grid its
entity ``offset``/``length`` use, which ``_slice_utf16`` already indexes on
(see ``test_telegram_entity_offsets.py``). The splitter measured the cut with
Python ``len``, which counts code points, so every non-BMP character was one
there and two on the wire. A reply made of emoji went out as a single message
of up to twice the cap and Telegram answered ``message is too long``; no send
path retries that error -- the ``parse entities`` fallback does not cover it,
and ``edit()`` and the caption path have no retry at all -- so the reply was
dropped rather than split.

The MS Teams adapter already measures its own cap this way, for the same
reason (``_measure_activity_text``, #2433).
"""

from __future__ import annotations

import pytest

from agentos.channels._telegram_formatting import render_telegram_html
from agentos.channels.telegram import (
    _MESSAGE_TEXT_LIMIT,
    TelegramChannel,
    TelegramChannelConfig,
    _measure_telegram_text,
    _utf16_length,
)

EMOJI = "\U0001f600"  # U+1F600, one code point, two UTF-16 units
MATH_A = "\U0001d400"  # U+1D400 MATHEMATICAL BOLD CAPITAL A


def _telegram_units(markdown: str) -> int:
    """What Telegram will count for the text this markdown renders to."""
    return _utf16_length(render_telegram_html(markdown))


def _chunks(content: str, *, auto_rendered: bool = True) -> list[str]:
    return TelegramChannel._split_message_for_send(content, auto_rendered=auto_rendered)


# ── the issue's reproduction ───────────────────────────────────────────────


@pytest.mark.parametrize("char", [EMOJI, MATH_A])
@pytest.mark.parametrize("count", [2500, 4000])
def test_a_non_bmp_reply_over_the_cap_is_split(char: str, count: int) -> None:
    """2500 emoji is 2500 code points and 5000 units: one message, 904 over."""
    content = char * count

    segments = _chunks(content)

    assert len(segments) > 1
    for segment in segments:
        assert _telegram_units(segment) <= _MESSAGE_TEXT_LIMIT


@pytest.mark.parametrize("char", [EMOJI, MATH_A])
def test_one_non_bmp_character_at_the_boundary_tips_the_message_over(char: str) -> None:
    """It takes exactly one: a message otherwise flush against the cap."""
    content = "a" * (_MESSAGE_TEXT_LIMIT - 1) + char

    segments = _chunks(content)

    assert _telegram_units(content) == _MESSAGE_TEXT_LIMIT + 1
    assert len(segments) == 2
    assert "".join(segments) == content


def test_every_chunk_of_a_long_mixed_reply_fits() -> None:
    """Prose with emoji in it, which is what a chat reply actually looks like."""
    paragraph = "Deployment finished " + EMOJI + " and the rollout is green " + EMOJI + ". "
    content = paragraph * 400

    segments = _chunks(content)

    assert len(segments) > 1
    assert "".join(segments) == content
    for segment in segments:
        assert _telegram_units(segment) <= _MESSAGE_TEXT_LIMIT


# ── the raw-text path (explicit parse_mode) counts the same way ────────────


def test_raw_text_is_measured_in_utf16_units_too() -> None:
    """``auto_rendered=False`` sends the text verbatim, so it is the text that
    has to fit -- still on Telegram's grid, not Python's."""
    content = EMOJI * 3000

    segments = _chunks(content, auto_rendered=False)

    assert len(segments) > 1
    for segment in segments:
        assert _utf16_length(segment) <= _MESSAGE_TEXT_LIMIT


# ── what must not change ───────────────────────────────────────────────────


def test_an_all_ascii_reply_splits_exactly_as_before() -> None:
    content = "a" * 5000

    segments = _chunks(content)

    assert len(segments) == 2
    assert "".join(segments) == content
    assert len(segments[0]) == _MESSAGE_TEXT_LIMIT


def test_a_short_reply_is_still_one_message() -> None:
    assert _chunks("hello " + EMOJI) == ["hello " + EMOJI]


def test_markup_is_still_charged_to_the_budget() -> None:
    """The rendered HTML, not the markdown, is what gets measured."""
    assert _measure_telegram_text("**bold**") == _utf16_length("<b>bold</b>")


def test_the_measure_matches_len_for_bmp_text() -> None:
    """Nothing moves for text that has no non-BMP character in it."""
    for text in ["", "plain", "café", "中文测试", "a b\tc\nd"]:
        assert _utf16_length(text) == len(text)


def test_a_surrogate_pair_is_never_split_down_the_middle() -> None:
    """The cut is made on the Python string, so a code point stays whole --
    pinned here because the measure now talks in half-code-point units."""
    content = EMOJI * 3000

    for segment in _chunks(content):
        assert segment.encode("utf-16-le").decode("utf-16-le") == segment
        assert len(segment) * 2 == _utf16_length(segment)


def test_an_explicit_measure_still_wins() -> None:
    head, tail = TelegramChannel._split_for_limit("abcdef", limit=3, measure=len)

    assert head == "abc"
    assert tail == "def"


def test_the_caption_limit_is_measured_the_same_way() -> None:
    """``sendDocument`` captions cap at 1024 and have no retry at all."""
    head, tail = TelegramChannel._split_for_limit(EMOJI * 800, limit=1024)

    assert tail, "800 emoji is 1600 units and must not ride as one caption"
    assert _telegram_units(head) <= 1024


def test_the_channel_still_constructs() -> None:
    assert TelegramChannel(TelegramChannelConfig(token="token")).config.name == "telegram"
