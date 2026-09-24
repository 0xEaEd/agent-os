"""Registry contract tests — the single source of truth for strategy metadata.

Every backend surface (dispatch, cache key, boot, doctor, mutations, CLI,
RoutingSource) consults this registry instead of comparing against literal
strategy ids, so these tests pin the values those surfaces depend on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentos.agentos_router.pilot.features import MINILM_MODEL_ID
from agentos.agentos_router.pilot.strategy import (
    SOURCE_HEALTHY,
    SOURCE_UNAVAILABLE,
)
from agentos.router_strategies import (
    JEV_STRATEGY_ID,
    LLM_JUDGE_STRATEGY_ID,
    PILOT_STRATEGY_ID,
    V4_STRATEGY_ID,
    RouterStrategyInfo,
    get_strategy_info,
    is_known_strategy,
    jev_credential_probe,
    known_strategy_ids,
    pilot_asset_probe,
    resolve_strategy_id,
)
from agentos.router_tiers import DEFAULT_ROUTER_STRATEGY


def test_registry_knows_all_strategies() -> None:
    assert known_strategy_ids() == {
        LLM_JUDGE_STRATEGY_ID,
        PILOT_STRATEGY_ID,
        JEV_STRATEGY_ID,
    }
    assert is_known_strategy("pilot-v1")
    # The legacy v4_phase3 engine was removed (Phase C); its id survives only
    # as a migration source and must NOT register as a live strategy.
    assert not is_known_strategy(V4_STRATEGY_ID)
    assert not is_known_strategy("nope")


def test_pilot_registry_entry_matches_strategy_source_tags() -> None:
    info = get_strategy_info(PILOT_STRATEGY_ID)
    assert isinstance(info, RouterStrategyInfo)
    # The registry telemetry tags must match the strategy's own constants.
    assert info.source == SOURCE_HEALTHY == "pilot_v1"
    assert info.degraded_source == SOURCE_UNAVAILABLE == "pilot_unavailable"
    assert info.requires_local_assets is True
    assert info.uses_judge is False
    assert info.asset_probe is pilot_asset_probe


def test_judge_registry_entry() -> None:
    judge = get_strategy_info(LLM_JUDGE_STRATEGY_ID)
    assert judge is not None
    assert judge.requires_local_assets is False
    assert judge.uses_judge is True
    assert judge.requires_remote_credentials is False
    assert judge.credential_probe is None


def test_jev_registry_entry_matches_strategy_source_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from agentos.agentos_router import jev

    info = get_strategy_info(JEV_STRATEGY_ID)
    assert isinstance(info, RouterStrategyInfo)
    assert info.source == jev.SOURCE_HEALTHY == "jev"
    assert info.degraded_source == jev.SOURCE_UNAVAILABLE == "jev_unavailable"
    assert info.requires_local_assets is False
    assert info.uses_judge is False
    assert info.asset_probe is None
    assert info.requires_remote_credentials is True
    assert info.credential_probe is jev_credential_probe

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    cfg = SimpleNamespace(jev=SimpleNamespace(api_key=None, api_key_env="TYPESAFE_API_KEY"))
    problem = info.credential_probe(cfg)
    assert problem is not None and "TYPESAFE_API_KEY" in problem

    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-x")
    assert info.credential_probe(cfg) is None
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    cfg_literal = SimpleNamespace(jev=SimpleNamespace(api_key="k", api_key_env="TYPESAFE_API_KEY"))
    assert info.credential_probe(cfg_literal) is None


def test_jev_is_never_the_default_strategy() -> None:
    assert DEFAULT_ROUTER_STRATEGY != JEV_STRATEGY_ID


def test_judge_registry_entry_has_no_asset_probe() -> None:
    judge = get_strategy_info(LLM_JUDGE_STRATEGY_ID)
    assert judge is not None
    assert judge.asset_probe is None


def test_registry_minilm_id_tracks_feature_builder() -> None:
    # The Pilot probe checks the MiniLM dir; its id must track the feature
    # builder's pinned model id so the probe never drifts.
    from agentos import router_strategies

    assert router_strategies._MINILM_MODEL_ID == MINILM_MODEL_ID


def test_resolve_strategy_id_falls_back_to_default() -> None:
    assert resolve_strategy_id("pilot-v1") == "pilot-v1"
    # v4_phase3 maps through LEGACY_STRATEGY_ALIASES to pilot-v1 explicitly —
    # the same target the config validator produces — independent of whatever
    # DEFAULT_ROUTER_STRATEGY happens to be.
    assert resolve_strategy_id("v4_phase3") == PILOT_STRATEGY_ID
    assert resolve_strategy_id("bogus") == DEFAULT_ROUTER_STRATEGY
    assert resolve_strategy_id(None) == DEFAULT_ROUTER_STRATEGY
    assert resolve_strategy_id("") == DEFAULT_ROUTER_STRATEGY


def test_pilot_asset_probe_reports_missing_bundle(tmp_path: Path) -> None:
    # Point at an empty dir: both bundle files must be reported missing.
    cfg = type("Cfg", (), {"pilot_artifact_dir": str(tmp_path / "absent")})()
    missing = pilot_asset_probe(cfg)
    assert any("model.onnx" in m for m in missing)
    assert any("manifest.json" in m for m in missing)


def test_pilot_asset_probe_reports_a_missing_runtime_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An install without the ml-router extra has the pilot package but not
    # numpy, so importing it raises. The probe must report that like any other
    # missing asset — boot warns and degrades — rather than let the ImportError
    # escape and take the gateway down with a traceback.
    from agentos import router_strategies

    def no_numpy() -> Path:
        raise ImportError("No module named 'numpy'", name="numpy")

    monkeypatch.setattr(router_strategies, "_pilot_default_artifact_dir", no_numpy)

    missing = router_strategies.pilot_asset_probe(None)

    assert len(missing) == 1
    assert "numpy" in missing[0]
    assert "ml-router" in missing[0]


def test_pilot_asset_probe_reports_partial_minilm_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A present-but-partial MiniLM dir (LFS not smudged: tokenizer.json missing)
    # must be reported per-file, not passed off as ready — otherwise boot/doctor
    # say ready while _MiniLMEncoder degrades on every turn.
    from agentos import router_strategies

    bundle = tmp_path / "pilot_v1"
    bundle.mkdir()
    (bundle / "model.onnx").write_bytes(b"")
    (bundle / "manifest.json").write_text("{}")

    partial_minilm = tmp_path / "all-MiniLM-L6-v2-int8"
    partial_minilm.mkdir()
    (partial_minilm / "model.onnx").write_bytes(b"")  # tokenizer.json absent

    monkeypatch.setattr(router_strategies, "_minilm_onnx_dir", lambda: partial_minilm)

    cfg = type("Cfg", (), {"pilot_artifact_dir": str(bundle)})()
    missing = router_strategies.pilot_asset_probe(cfg)

    # Pilot bundle files are present; only the missing MiniLM tokenizer.json is
    # reported (by its concrete path).
    assert not [m for m in missing if "manifest.json" in m]
    assert any(m.endswith("tokenizer.json") for m in missing)
    assert not any(m.endswith("model.onnx") and "MiniLM" not in m and "pilot" in m for m in missing)


def test_pilot_asset_probe_passes_for_complete_minilm_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A complete MiniLM dir (both required files present) adds nothing to the
    # missing list.
    from agentos import router_strategies

    bundle = tmp_path / "pilot_v1"
    bundle.mkdir()
    (bundle / "model.onnx").write_bytes(b"")
    (bundle / "manifest.json").write_text("{}")

    minilm = tmp_path / "all-MiniLM-L6-v2-int8"
    minilm.mkdir()
    (minilm / "model.onnx").write_bytes(b"")
    (minilm / "tokenizer.json").write_text("{}")

    monkeypatch.setattr(router_strategies, "_minilm_onnx_dir", lambda: minilm)

    cfg = type("Cfg", (), {"pilot_artifact_dir": str(bundle)})()
    assert router_strategies.pilot_asset_probe(cfg) == []


def test_pilot_asset_probe_passes_for_fixture_bundle() -> None:
    # The committed fixture bundle satisfies the file checks; the MiniLM dir is
    # bundled too, so a healthy tree yields no missing pilot bundle files.
    fixture = Path(__file__).parent / "test_agentos_router" / "data" / "pilot_fixture"
    cfg = type("Cfg", (), {"pilot_artifact_dir": str(fixture)})()
    missing = pilot_asset_probe(cfg)
    # No bundle file should be missing (MiniLM presence depends on the checkout;
    # assert only on the bundle-file portion).
    assert not [m for m in missing if "model.onnx" in m or "manifest.json" in m]
