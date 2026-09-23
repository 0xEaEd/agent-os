"""Tests for the shared onboarding setup engine."""

import tomllib

from agentos.onboarding.setup_engine import SetupEngine


def test_setup_engine_applies_provider_and_router_without_persisting_secret(tmp_path):
    target = tmp_path / "config.toml"
    engine = SetupEngine(path=target)

    engine.apply(
        "provider",
        {
            "providerId": "deepseek",
            "model": "deepseek-chat",
            "apiKeyEnv": "DEEPSEEK_API_KEY",
        },
    )
    engine.apply("router", {"mode": "recommended"})
    result = engine.persist()

    data = tomllib.loads(target.read_text())
    assert result.path == target
    assert data["llm"]["provider"] == "deepseek"
    assert data["llm"]["api_key_env"] == "DEEPSEEK_API_KEY"
    assert "api_key" not in data["llm"]
    assert data["agentos_router"]["tier_profile"] == "deepseek"
    assert "tiers" not in data["agentos_router"]


def test_setup_engine_can_derive_provider_model_from_router_default_tier(tmp_path):
    target = tmp_path / "config.toml"
    engine = SetupEngine(path=target)

    engine.apply(
        "provider",
        {
            "providerId": "deepseek",
            "apiKeyEnv": "DEEPSEEK_API_KEY",
        },
    )
    result = engine.persist()

    data = tomllib.loads(target.read_text())
    assert result.path == target
    assert data["llm"]["provider"] == "deepseek"
    assert data["llm"]["model"] == "deepseek-v4-flash"
    assert data["agentos_router"]["tier_profile"] == "deepseek"
    assert data["agentos_router"]["default_tier"] == "c1"


def test_setup_engine_router_tier_override_updates_direct_fallback_model(tmp_path):
    target = tmp_path / "config.toml"
    engine = SetupEngine(path=target)

    engine.apply(
        "provider",
        {
            "providerId": "openai",
            "apiKeyEnv": "OPENAI_API_KEY",
        },
    )
    engine.apply(
        "router",
        {
            "mode": "recommended",
            "defaultTier": "c2",
            "tiers": {
                "c2": {
                    "provider": "openai",
                    "model": "gpt-5.5-custom",
                    "thinkingLevel": "high",
                }
            },
        },
    )
    engine.persist()

    data = tomllib.loads(target.read_text())
    assert data["llm"]["model"] == "gpt-5.5-custom"
    assert data["agentos_router"]["tier_profile"] == "openai"
    assert data["agentos_router"]["default_tier"] == "c2"
    assert data["agentos_router"]["tiers"]["c2"]["model"] == "gpt-5.5-custom"
    assert data["agentos_router"]["tiers"]["c2"]["thinking_level"] == "high"


def test_setup_engine_threads_judge_model_through_router_apply(tmp_path):
    target = tmp_path / "config.toml"
    engine = SetupEngine(path=target)

    engine.apply(
        "provider",
        {
            "providerId": "deepseek",
            "model": "deepseek-chat",
            "apiKeyEnv": "DEEPSEEK_API_KEY",
        },
    )
    res = engine.apply(
        "router",
        {"mode": "recommended", "judgeModel": "deepseek-v4-pro"},
    )
    engine.persist()

    assert res.public_payload["judge"]["judge_model"] == "deepseek-v4-pro"
    data = tomllib.loads(target.read_text())
    assert data["agentos_router"]["judge_model"] == "deepseek-v4-pro"

    # Auto clears the persisted pin so profile switches auto-update the judge.
    res = engine.apply("router", {"mode": "recommended", "judgeModel": "auto"})
    engine.persist()

    assert res.public_payload["judge"]["judge_model"] is None
    assert res.public_payload["judge"]["source"] == "auto"
    data = tomllib.loads(target.read_text())
    assert "judge_model" not in data["agentos_router"]


def test_setup_engine_router_catalog_includes_judge_block():
    engine = SetupEngine.__new__(SetupEngine)  # catalog is config-independent

    payload = engine.catalog("router")

    judge = payload["routerProfiles"]["judge"]
    assert "profiles" in judge
    assert judge["profiles"]["deepseek"]["autoModel"] == "deepseek-v4-flash"


def test_setup_engine_next_steps_do_not_include_secret(tmp_path):
    engine = SetupEngine(path=tmp_path / "config.toml")
    engine.apply(
        "provider",
        {
            "providerId": "openrouter",
            "model": "deepseek/deepseek-v4-flash",
            "apiKey": "sk-secret",
        },
    )

    text = engine.preview_next_steps()

    assert "sk-secret" not in text
    assert "agentos gateway start" in text
    assert "openrouter" in text


def test_setup_engine_image_generation_can_use_custom_env_reference(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("AGENTOS_TEST_IMAGE_KEY", "sk-image-env")
    target = tmp_path / "config.toml"
    engine = SetupEngine(path=target)

    engine.apply(
        "image-generation",
        {
            "providerId": "openrouter",
            "primary": "openrouter/google/gemini-3.1-flash-image-preview",
            "apiKeyEnv": "AGENTOS_TEST_IMAGE_KEY",
        },
    )
    engine.persist()

    data = tomllib.loads(target.read_text())
    provider = data["image_generation"]["providers"]["openrouter"]
    assert provider["api_key"] == ""
    assert provider["api_key_env"] == "AGENTOS_TEST_IMAGE_KEY"


def test_setup_engine_accepts_short_capability_section_aliases(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    target = tmp_path / "config.toml"
    engine = SetupEngine(path=target)

    engine.apply("image", {"enabled": False})
    engine.apply(
        "memory",
        {
            "providerId": "local",
            "onnxDir": "models/bge",
        },
    )
    engine.persist()

    data = tomllib.loads(target.read_text())
    assert data["image_generation"]["enabled"] is False
    assert data["memory"]["embedding"]["provider"] == "local"
    assert data["memory"]["embedding"]["local"]["onnx_dir"] == "models/bge"


def test_setup_engine_catalog_includes_memory_embedding():
    engine = SetupEngine()

    payload = engine.catalog("memory-embedding")

    provider_ids = {p["providerId"] for p in payload["memoryEmbeddingProviders"]}
    assert {"auto", "local", "openai", "ollama", "none"} <= provider_ids
    assert all("whatYouNeed" in p for p in payload["memoryEmbeddingProviders"])


def test_setup_engine_forwards_jev_fields_and_verifies_the_key(tmp_path, monkeypatch):
    import agentos.agentos_router.jev as jev_mod

    probed: list[str] = []

    def _fake_probe(api_key, **_kwargs):
        probed.append(api_key)
        return None

    monkeypatch.setattr(jev_mod, "probe_jev", _fake_probe)
    target = tmp_path / "config.toml"
    engine = SetupEngine(path=target)

    engine.apply("provider", {"providerId": "deepseek", "apiKeyEnv": "DEEPSEEK_API_KEY"})
    res = engine.apply(
        "router",
        {
            "mode": "recommended",
            "strategy": "jev",
            "jevApiKey": "tsk-secret",
            "jevApiKeyEnv": "MY_TYPESAFE_KEY",
            "jevHighRiskThreshold": 0.9,
        },
    )
    engine.persist()

    assert probed == ["tsk-secret"]
    assert res.public_payload["jev"] == {
        "api_key_configured": True,
        "api_key_env": "MY_TYPESAFE_KEY",
    }
    data = tomllib.loads(target.read_text())
    assert data["agentos_router"]["strategy"] == "jev"
    assert data["agentos_router"]["jev"]["api_key"] == "tsk-secret"
    assert data["agentos_router"]["jev"]["api_key_env"] == "MY_TYPESAFE_KEY"
    assert data["agentos_router"]["jev"]["high_risk_threshold"] == 0.9


def test_setup_engine_jev_without_a_new_key_does_not_probe(tmp_path, monkeypatch):
    import agentos.agentos_router.jev as jev_mod

    def _boom(*_args, **_kwargs):
        raise AssertionError("probe_jev must not run when no key is supplied")

    monkeypatch.setattr(jev_mod, "probe_jev", _boom)
    engine = SetupEngine(path=tmp_path / "config.toml")

    engine.apply("provider", {"providerId": "deepseek", "apiKeyEnv": "DEEPSEEK_API_KEY"})
    res = engine.apply("router", {"mode": "recommended", "strategy": "jev"})

    assert res.config.agentos_router.strategy == "jev"
