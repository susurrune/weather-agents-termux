"""Tests for configuration system."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
import yaml


# ── Unit tests for config logic ─────────────────────────────────────────────

class TestConfigCore:
    def test_load_model_catalog(self):
        from weather_agents.core.config import load_model_catalog
        catalog = load_model_catalog()
        assert isinstance(catalog, dict)
        # Should have at least one provider
        assert len(catalog) > 0

    def test_model_catalog_has_expected_providers(self):
        from weather_agents.core.config import load_model_catalog
        catalog = load_model_catalog()
        providers = {k.lower() for k in catalog}
        assert "openai" in providers or "anthropic" in providers

    def test_set_and_delete_config(self):
        from weather_agents.core.config import set_config, delete_config, load_config

        # Set temperature
        ok, msg = set_config("temperature", "0.33")
        assert ok, msg

        # Verify it took effect
        cfg = load_config()
        assert cfg.llm.temperature == 0.33

        # Delete temperature
        ok, msg = delete_config("temperature")
        assert ok, msg

        # Verify it reverted to default
        cfg = load_config()
        assert cfg.llm.temperature == 0.7

    def test_set_model_config(self):
        from weather_agents.core.config import set_config, delete_config, load_config

        set_config("model.fog", "gpt-4o")
        cfg = load_config()
        assert cfg.agents.fog.model == "gpt-4o"

        delete_config("model.fog")
        cfg = load_config()
        assert cfg.agents.fog.model is None

    def test_set_api_key(self):
        from weather_agents.core.config import set_config, delete_config, load_config

        set_config("api_key.openai", "sk-test-key-123")
        cfg = load_config()
        assert cfg.llm.api_keys.get("openai") == "sk-test-key-123"

        delete_config("api_key.openai")
        cfg = load_config()
        assert "openai" not in cfg.llm.api_keys

    def test_set_invalid_key(self):
        from weather_agents.core.config import set_config
        ok, msg = set_config("invalid.key", "value")
        assert not ok

    def test_delete_nonexistent(self):
        from weather_agents.core.config import delete_config
        ok, msg = delete_config("temperature")  # already deleted by earlier test
        assert ok  # should still say OK (already at default)

    def test_set_unknown_agent(self):
        from weather_agents.core.config import set_config
        ok, msg = set_config("model.nonexistent", "gpt-4o")
        assert not ok

    def test_set_default_model(self):
        from weather_agents.core.config import set_config, delete_config, load_config

        set_config("default_model", "gpt-4o")
        cfg = load_config()
        assert cfg.llm.default_model == "gpt-4o"

        delete_config("default_model")
        cfg = load_config()
        assert cfg.llm.default_model == "gpt-4o-mini"
