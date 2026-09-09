"""Contract for the one shared compaction provider/model resolution.

``_effective_compaction_model`` and ``_resolve_compaction_provider`` were
copy-pasted between ``rpc_chat``, ``rpc_sessions``, and the standalone slash
adapter. Provider resolution decides which key and which model a compaction
run bills, so a copy drifting from its siblings is a live-money bug that no
test would catch. These tests pin the single implementation.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import agentos
from agentos.session.compaction import (
    effective_compaction_model,
    resolve_compaction_provider,
)


class _Selector:
    """A provider selector that records how compaction used it."""

    def __init__(
        self,
        *,
        clone_raises: bool = False,
        override_raises: bool = False,
        resolve_raises: bool = False,
        resolvable: bool = True,
    ) -> None:
        self.overrides: list[str] = []
        self.clones: list[_Selector] = []
        self._clone_raises = clone_raises
        self._override_raises = override_raises
        self._resolve_raises = resolve_raises
        self._resolvable = resolvable

    def clone(self) -> _Selector:
        if self._clone_raises:
            raise RuntimeError("clone failed")
        twin = _Selector(
            override_raises=self._override_raises,
            resolve_raises=self._resolve_raises,
            resolvable=self._resolvable,
        )
        self.clones.append(twin)
        return twin

    def override_model(self, model: str) -> None:
        if self._override_raises:
            raise RuntimeError("override failed")
        self.overrides.append(model)

    def resolve(self) -> object:
        if self._resolve_raises:
            raise RuntimeError("resolve failed")
        return SimpleNamespace(provider_name="stub", owner=self)


class TestEffectiveCompactionModel:
    def test_none_session_has_no_opinion(self) -> None:
        assert effective_compaction_model(None) is None

    def test_override_wins_over_session_model(self) -> None:
        session = SimpleNamespace(model="base/model", model_override="override/model")
        assert effective_compaction_model(session) == "override/model"

    def test_falls_back_to_session_model(self) -> None:
        session = SimpleNamespace(model="base/model", model_override=None)
        assert effective_compaction_model(session) == "base/model"

    def test_session_without_either_attribute(self) -> None:
        assert effective_compaction_model(SimpleNamespace()) is None


class TestResolveCompactionProvider:
    def test_no_selector_resolves_to_nothing(self) -> None:
        assert resolve_compaction_provider(None, "some/model") is None

    def test_override_is_applied_to_the_clone_not_the_live_selector(self) -> None:
        selector = _Selector()

        provider = resolve_compaction_provider(selector, "override/model")

        assert selector.overrides == [], "the live selector must not be re-pointed"
        assert [c.overrides for c in selector.clones] == [["override/model"]]
        assert provider is not None
        assert provider.owner is selector.clones[0]

    def test_no_model_override_leaves_the_clone_alone(self) -> None:
        selector = _Selector()

        resolve_compaction_provider(selector, None)

        assert [c.overrides for c in selector.clones] == [[]]

    def test_uncloneable_selector_is_used_directly_without_override(self) -> None:
        resolved = object()
        selector = SimpleNamespace(resolve=lambda: resolved)  # no ``clone``

        # No clone to isolate the override into, so the live selector is left
        # as it is rather than re-pointed under the running turn.
        assert resolve_compaction_provider(selector, "override/model") is resolved

    def test_clone_failure_falls_back_to_the_live_selector(self) -> None:
        selector = _Selector(clone_raises=True)

        provider = resolve_compaction_provider(selector, "override/model")

        assert selector.overrides == []
        assert provider is not None
        assert provider.owner is selector

    def test_override_failure_still_resolves(self) -> None:
        selector = _Selector(override_raises=True)

        assert resolve_compaction_provider(selector, "override/model") is not None

    def test_resolve_failure_degrades_to_none(self) -> None:
        selector = _Selector(resolve_raises=True)

        assert resolve_compaction_provider(selector, "override/model") is None

    def test_selector_without_resolve_degrades_to_none(self) -> None:
        assert resolve_compaction_provider(SimpleNamespace(), "override/model") is None


class TestSingleImplementation:
    def test_no_module_redefines_the_helpers(self) -> None:
        src_root = Path(agentos.__file__).parent
        canonical = src_root / "session" / "compaction.py"
        names = {"effective_compaction_model", "resolve_compaction_provider"}
        offenders: list[str] = []
        for path in sorted(src_root.rglob("*.py")):
            if path == canonical:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                if node.name.lstrip("_") not in names:
                    continue
                # A one-line delegating shim (``return _bridge.resolve(...)``)
                # is the adapter layering, not a second implementation.
                body = [n for n in node.body if not isinstance(n, ast.Expr)]
                if len(body) == 1 and isinstance(body[0], ast.Return):
                    continue
                offenders.append(f"{path.relative_to(src_root.parent)}:{node.lineno}")
        assert offenders == [], (
            "compaction provider resolution lives only in agentos/session/compaction.py; "
            f"found copies at {offenders}"
        )


def test_gateway_and_cli_share_the_canonical_helper() -> None:
    """The former copy sites now reference the shared implementation."""
    from agentos.cli.tui.adapters import slash_standalone
    from agentos.gateway import rpc_chat, rpc_sessions

    assert slash_standalone._resolve_compaction_provider is resolve_compaction_provider
    for module in (rpc_chat, rpc_sessions):
        assert module.resolve_compaction_provider is resolve_compaction_provider
        assert module.effective_compaction_model is effective_compaction_model
