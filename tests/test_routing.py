"""Comprehensive tests for route_model(), _detect_engine(), and _default_config()."""

import json

import pytest

from aya.workspace import (
    Workspace,
    _default_config,
    _detect_engine,
    route_model,
)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """Redirect AYA_HOME to tmp so tests don't pollute ~/.aya/"""
    fake_home = tmp_path / "aya-home"
    monkeypatch.setattr("aya.workspace.AYA_HOME", fake_home)
    monkeypatch.setattr("aya.workspace.REGISTRY_PATH", fake_home / "registry.json")
    project = tmp_path / "project"
    project.mkdir()
    w = Workspace(str(project))
    w.init("test-project")
    # route_model() creates Workspace("."), so cwd must be the project dir
    monkeypatch.chdir(project)
    return w


ALL_TASK_TYPES = [
    "architecture",
    "complex_refactor",
    "implementation",
    "testing",
    "boilerplate",
    "review",
    "documentation",
    "debugging",
]


class TestRouteModelComprehensive:
    def test_all_8_task_types(self, ws):
        for tt in ALL_TASK_TYPES:
            result = route_model(tt)
            assert isinstance(result, dict)
            for key in ("model", "engine", "fallback", "task_type"):
                assert key in result, f"missing key '{key}' for task_type={tt}"

    def test_architecture_routes_to_opus(self, ws):
        r = route_model("architecture")
        assert r["model"] == "claude-opus"

    def test_implementation_routes_to_deepseek(self, ws):
        r = route_model("implementation")
        assert r["model"] == "deepseek-v4-pro"

    def test_testing_routes_to_gpt(self, ws):
        r = route_model("testing")
        assert r["model"] == "gpt-5.5"

    def test_debugging_routes_to_opus(self, ws):
        r = route_model("debugging")
        assert r["model"] == "claude-opus"

    def test_review_routes_to_sonnet(self, ws):
        r = route_model("review")
        assert r["model"] == "claude-sonnet"

    def test_documentation_routes_to_deepseek(self, ws):
        r = route_model("documentation")
        assert r["model"] == "deepseek-v4-pro"

    def test_unknown_type_fallback(self, ws):
        r = route_model("unknown_xyz")
        assert r["model"] == "claude-sonnet"
        assert r["engine"] == "claude-agent"

    def test_empty_string_type(self, ws):
        r = route_model("")
        assert r["model"] == "claude-sonnet"
        assert r["engine"] == "claude-agent"

    def test_custom_routing_rules(self, ws):
        config = ws.load_config()
        config["routing_rules"].append(
            {"task_type": "my_custom", "prefer": "gpt-5.5", "fallback": "claude-haiku"}
        )
        ws._write_json(ws.runtime_dir / "config.json", config)

        r = route_model("my_custom")
        assert r["model"] == "gpt-5.5"
        assert r["engine"] == "codex"
        assert r["fallback"] == "claude-haiku"

    def test_empty_routing_rules(self, ws):
        config = ws.load_config()
        config["routing_rules"] = []
        ws._write_json(ws.runtime_dir / "config.json", config)

        r = route_model("architecture")
        assert r["model"] == "claude-sonnet"
        assert r["engine"] == "claude-agent"

    def test_fallback_key_present(self, ws):
        for tt in ALL_TASK_TYPES:
            r = route_model(tt)
            assert "fallback" in r


class TestDetectEngine:
    def test_gpt_prefix(self):
        assert _detect_engine("gpt-5.5") == "codex"

    def test_o1_prefix(self):
        assert _detect_engine("o1-preview") == "codex"

    def test_o3_prefix(self):
        assert _detect_engine("o3-mini") == "codex"

    def test_claude_prefix(self):
        assert _detect_engine("claude-sonnet") == "claude-agent"

    def test_opus_prefix(self):
        assert _detect_engine("opus") == "claude-agent"

    def test_sonnet_prefix(self):
        assert _detect_engine("sonnet-4.6") == "claude-agent"

    def test_haiku_prefix(self):
        assert _detect_engine("haiku") == "claude-agent"

    def test_unknown_model_fallback(self):
        assert _detect_engine("deepseek-v4-pro") == "claude-cli"

    def test_empty_string(self):
        assert _detect_engine("") == "claude-cli"

    def test_case_insensitive(self):
        # _detect_engine lowercases the input, so uppercase matches fine
        assert _detect_engine("GPT-5.5") == "codex"
        assert _detect_engine("Claude-Opus") == "claude-agent"
        assert _detect_engine("HAIKU") == "claude-agent"


class TestDefaultConfig:
    def test_has_5_models(self):
        config = _default_config()
        assert len(config["models"]) == 5

    def test_has_8_routing_rules(self):
        config = _default_config()
        assert len(config["routing_rules"]) == 8

    def test_all_models_have_required_keys(self):
        required = {
            "engine",
            "model_id",
            "capabilities",
            "swe_bench_verified",
            "cost_input_per_mtok",
            "cost_output_per_mtok",
        }
        config = _default_config()
        for name, model in config["models"].items():
            missing = required - set(model.keys())
            assert not missing, f"model '{name}' missing keys: {missing}"

    def test_all_routing_rules_have_required_keys(self):
        required = {"task_type", "prefer", "fallback"}
        config = _default_config()
        for i, rule in enumerate(config["routing_rules"]):
            missing = required - set(rule.keys())
            assert not missing, f"routing_rules[{i}] missing keys: {missing}"

    def test_all_costs_positive(self):
        config = _default_config()
        for name, model in config["models"].items():
            assert model["cost_input_per_mtok"] > 0, f"{name} input cost <= 0"
            assert model["cost_output_per_mtok"] > 0, f"{name} output cost <= 0"
