"""Issue #2265: title-card-image render.py text wrapping with non-CJK scripts.

Scripts using spaces (Hangul, Cyrillic, Greek, Latin, emoji) must wrap on
whitespace boundaries rather than being sliced at fixed character counts by
the CJK ideograph chunker.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SRC = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "agentos"
    / "skills"
    / "bundled"
    / "title-card-image"
    / "scripts"
    / "render.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("title_card_render_under_test", _SRC)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_is_cjk_ideograph_distinguishes_scripts() -> None:
    mod = _load_module()
    # True for CJK ideographs
    assert mod._is_cjk_ideograph("中") is True
    assert mod._is_cjk_ideograph("文") is True
    assert mod._is_cjk_ideograph("\u4e00") is True
    assert mod._is_cjk_ideograph("\u9fff") is True
    assert mod._is_cjk_ideograph("\u3400") is True
    assert mod._is_cjk_ideograph("\uf900") is True

    # False for Hangul, Cyrillic, Latin, and Emoji
    assert mod._is_cjk_ideograph("안") is False
    assert mod._is_cjk_ideograph("녕") is False
    assert mod._is_cjk_ideograph("П") is False
    assert mod._is_cjk_ideograph("A") is False
    assert mod._is_cjk_ideograph("🚀") is False


def test_hangul_wraps_on_whitespace() -> None:
    mod = _load_module()
    text = "안녕하세요 반갑습니다 오늘 날씨가 참 좋습니다"
    lines = mod._wrap_text(text, max_chars=12)
    # Should wrap at word boundaries, not cut mid-syllable
    for line in lines:
        assert len(line) <= 12
    assert " ".join(lines) == text


def test_cyrillic_wraps_on_whitespace() -> None:
    mod = _load_module()
    text = "Привет мир как ваши дела сегодня"
    lines = mod._wrap_text(text, max_chars=15)
    for line in lines:
        assert len(line) <= 15
    assert " ".join(lines) == text


def test_emoji_and_latin_wrap_on_whitespace() -> None:
    mod = _load_module()
    text = "🚀 Launching new product 🌟 today"
    lines = mod._wrap_text(text, max_chars=15)
    for line in lines:
        assert len(line) <= 15
    assert " ".join(lines) == text


def test_cjk_ideographs_wrap_at_character_limit() -> None:
    mod = _load_module()
    text = "这是一个很长的中文字符串用于测试换行逻辑"
    lines = mod._wrap_text(text, max_chars=6)
    assert lines == ["这是一个很长", "的中文字符串", "用于测试换行", "逻辑"]


def test_explicit_newlines_preserved() -> None:
    mod = _load_module()
    text = "Line 1\nLine 2\nLine 3"
    assert mod._wrap_text(text, max_chars=20) == ["Line 1", "Line 2", "Line 3"]


# Kana and multi-line wrapping (#2439)


def test_is_kana_distinguishes_scripts() -> None:
    mod = _load_module()
    # True for Japanese kana
    assert mod._is_kana("あ") is True  # Hiragana
    assert mod._is_kana("ア") is True  # Katakana
    assert mod._is_kana("ー") is True  # Katakana prolonged sound mark
    assert mod._is_kana("ㇰ") is True  # Katakana Phonetic Extensions
    assert mod._is_kana("ｱ") is True  # Halfwidth Katakana (U+FF71)

    # False for Hangul (#2265), CJK ideographs, Latin, and Emoji
    assert mod._is_kana("안") is False
    assert mod._is_kana("ᄀ") is False  # Hangul Jamo
    assert mod._is_kana("中") is False
    assert mod._is_kana("A") is False
    assert mod._is_kana("🚀") is False


def test_hiragana_wraps_at_character_limit() -> None:
    mod = _load_module()
    text = "あいうえおかきくけこさしすせそたちつてと"
    lines = mod._wrap_text(text, max_chars=10)
    assert lines == ["あいうえおかきくけこ", "さしすせそたちつてと"]


def test_katakana_wraps_at_character_limit() -> None:
    mod = _load_module()
    text = "アイウエオカキクケコサシスセソタチツテト"
    lines = mod._wrap_text(text, max_chars=10)
    assert lines == ["アイウエオカキクケコ", "サシスセソタチツテト"]


def test_halfwidth_katakana_wraps_at_character_limit() -> None:
    mod = _load_module()
    text = "ｱｲｳｴｵｶｷｸｹｺ"
    assert mod._wrap_text(text, max_chars=4) == ["ｱｲｳｴ", "ｵｶｷｸ", "ｹｺ"]


def test_latin_words_wrap_on_whitespace() -> None:
    mod = _load_module()
    text = "The quick brown fox jumps over the lazy dog"
    lines = mod._wrap_text(text, max_chars=15)
    assert lines == ["The quick brown", "fox jumps over", "the lazy dog"]


def test_explicit_newline_lines_are_wrapped() -> None:
    mod = _load_module()
    text = "Line 1 is short\nLine 2 is somewhat longer and needs wrapping\nLine 3"
    assert mod._wrap_text(text, max_chars=20) == [
        "Line 1 is short",
        "Line 2 is somewhat",
        "longer and needs",
        "wrapping",
        "Line 3",
    ]


def test_explicit_newline_kana_lines_are_wrapped() -> None:
    mod = _load_module()
    text = "あいうえおかきくけこ\nアイウエオ"
    lines = mod._wrap_text(text, max_chars=4)
    assert lines == ["あいうえ", "おかきく", "けこ", "アイウエ", "オ"]


def test_explicit_blank_lines_preserved() -> None:
    mod = _load_module()
    assert mod._wrap_text("Top\n\nBottom", max_chars=10) == ["Top", "", "Bottom"]


def test_overlong_word_is_split_by_character() -> None:
    mod = _load_module()
    lines = mod._wrap_text("go supercalifragilistic now", max_chars=8)
    assert lines == ["go", "supercal", "ifragili", "stic now"]


def test_empty_text() -> None:
    mod = _load_module()
    assert mod._wrap_text("", max_chars=10) == [""]


def test_render_main_writes_kana_png(tmp_path: Path, monkeypatch) -> None:
    mod = _load_module()
    out_file = tmp_path / "card.png"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "render.py",
            "--text",
            "あいうえおかきくけこさしすせそたちつてと",
            "--subtitle",
            "日本語サブタイトル",
            "--output",
            str(out_file),
        ],
    )
    assert mod.main() == 0
    assert out_file.is_file()
    assert out_file.stat().st_size > 0
