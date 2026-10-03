"""Issue #3606: the newest-Node sort was a no-op for fnm.

#3604 added `_fallback_bin_dirs` so an `npm install -g agent-browser` under a
version manager is found when the gateway inherits a bare PATH, and
`_newest_first` so the newest Node's copy wins. `_version_key` read the
version from the directory above the leaf, which is right for nvm's
`.../versions/node/v24.16.0/bin` and wrong for fnm's
`.../node-versions/v24.16.0/installation/bin` -- there the parent of `bin` is
`installation`, so every fnm candidate keyed to `()`, the sort had nothing to
order by, and `glob` order decided which Node's binary was used.
"""

from __future__ import annotations

import os

import pytest

from agentos.tools.agent_browser import _fallback_bin_dirs, _newest_first, _version_key

VERSIONS = ["v9.11.2", "v18.20.4", "v20.11.1", "v24.16.0"]
NEWEST_FIRST = ["v24.16.0", "v20.11.1", "v18.20.4", "v9.11.2"]


def _nvm(version: str) -> str:
    return f"/h/.nvm/versions/node/{version}/bin"


def _fnm(version: str) -> str:
    return f"/h/.local/share/fnm/node-versions/{version}/installation/bin"


# ── the issue's reproduction ───────────────────────────────────────────────


@pytest.mark.parametrize("layout", [_nvm, _fnm], ids=["nvm", "fnm"])
def test_every_layout_keys_on_its_version(layout) -> None:
    assert [_version_key(layout(v)) for v in VERSIONS] == [
        (9, 11, 2),
        (18, 20, 4),
        (20, 11, 1),
        (24, 16, 0),
    ]


@pytest.mark.parametrize("layout", [_nvm, _fnm], ids=["nvm", "fnm"])
def test_every_layout_sorts_newest_first(layout) -> None:
    paths = [layout(v) for v in VERSIONS]

    ordered = sorted(paths, key=_version_key, reverse=True)

    assert ordered == [layout(v) for v in NEWEST_FIRST]


def test_the_two_layouts_agree() -> None:
    """The asymmetry itself: the same versions, two directory shapes, one order."""
    assert [_version_key(_nvm(v)) for v in VERSIONS] == [
        _version_key(_fnm(v)) for v in VERSIONS
    ]


def test_a_major_version_is_compared_as_a_number_not_a_string() -> None:
    """`v9` after `v18` is the ordering a lexicographic sort gets wrong."""
    assert _version_key(_fnm("v9.11.2")) < _version_key(_fnm("v18.20.4"))


# ── what must not change ───────────────────────────────────────────────────


def test_a_release_sorts_above_its_prerelease() -> None:
    assert _version_key(_nvm("v25.0.0")) > _version_key(_nvm("v25.0.0-rc.1"))


def test_a_directory_with_no_version_keys_empty() -> None:
    assert _version_key("/usr/local/bin") == ()
    assert _version_key("") == ()


def test_a_version_without_the_v_prefix_still_parses() -> None:
    assert _version_key("/opt/node/18.20.4/bin") == (18, 20, 4)


def test_a_windows_path_separator_is_handled() -> None:
    path = "C:" + chr(92) + "nvm" + chr(92) + "v24.16.0" + chr(92) + "bin"

    assert _version_key(path) == (24, 16, 0)


def test_a_numeric_directory_left_of_the_version_does_not_win() -> None:
    """The search runs right to left, so the version nearest the leaf wins and
    a numeric parent directory earlier in the path cannot shadow it."""
    assert _version_key("/srv/2024/node-versions/v24.16.0/installation/bin") == (24, 16, 0)


def test_newest_first_returns_a_list_and_tolerates_no_matches() -> None:
    assert _newest_first(os.path.join("/nonexistent-xyz", "*", "bin")) == []


def test_the_fallback_dirs_still_resolve_and_dedupe() -> None:
    dirs = _fallback_bin_dirs()

    assert isinstance(dirs, list)
    assert all(d for d in dirs), "no empty entries"
    assert len(dirs) == len(set(dirs)), "deduped"
