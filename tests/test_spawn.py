"""Tests for generate_spawn_command() and get_model_env()."""

import json
from pathlib import Path

import pytest

from aya.models import create_task
from aya.workspace import (
    Workspace,
    generate_spawn_command,
    get_model_env,
    save_models,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ws(tmp_path, monkeypatch):
    """Redirect AYA_HOME to tmp so tests don't pollute ~/.aya/"""
    fake_home = tmp_path / "aya-home"
    monkeypatch.setattr("aya.workspace.AYA_HOME", fake_home)
    monkeypatch.setattr("aya.workspace.REGISTRY_PATH", fake_home / "registry.json")
    monkeypatch.setattr("aya.workspace.MODELS_FILE", fake_home / "models.json")
    project = tmp_path / "project"
    project.mkdir()
    w = Workspace(str(project))
    w.init("test-project")
    return w


@pytest.fixture
def models_file(tmp_path, monkeypatch):
    """Redirect MODELS_FILE to a temp location and return the path."""
    mf = tmp_path / "models.json"
    monkeypatch.setattr("aya.workspace.MODELS_FILE", mf)
    return mf


# Common args shared across many spawn tests
_COMMON = dict(
    task_id="task-001",
    worker_id="worker-task-001",
    worktree_path="/tmp/wt",
    runtime_dir="/tmp/rt",
    prompt_file="/tmp/rt/prompts/worker-task-001.md",
)


# ---------------------------------------------------------------------------
# TestGenerateSpawnCommand
# ---------------------------------------------------------------------------

class TestGenerateSpawnCommand:

    def test_claude_agent_returns_agent_type(self):
        result = generate_spawn_command(
            model="claude-sonnet", engine="claude-agent", **_COMMON
        )
        assert result["type"] == "agent"

    def test_claude_agent_model_alias(self):
        result = generate_spawn_command(
            model="claude-sonnet", engine="claude-agent", **_COMMON
        )
        assert result["command"]["model"] == "sonnet"

    def test_claude_agent_opus_alias(self):
        result = generate_spawn_command(
            model="claude-opus", engine="claude-agent", **_COMMON
        )
        assert result["command"]["model"] == "opus"

    def test_claude_agent_has_background(self):
        result = generate_spawn_command(
            model="claude-sonnet", engine="claude-agent", **_COMMON
        )
        assert result["command"]["run_in_background"] is True

    def test_claude_agent_has_bypass(self):
        result = generate_spawn_command(
            model="claude-sonnet", engine="claude-agent", **_COMMON
        )
        assert result["command"]["mode"] == "bypassPermissions"

    def test_claude_cli_returns_bash_type(self, models_file):
        result = generate_spawn_command(
            model="deepseek-v4-pro", engine="claude-cli", **_COMMON
        )
        assert result["type"] == "bash"

    def test_claude_cli_contains_model(self, models_file):
        result = generate_spawn_command(
            model="deepseek-v4-pro", engine="claude-cli", **_COMMON
        )
        assert "--model deepseek-v4-pro" in result["command"]

    def test_claude_cli_contains_output_redirect(self, models_file):
        result = generate_spawn_command(
            model="deepseek-v4-pro", engine="claude-cli", **_COMMON
        )
        assert "> /tmp/rt/logs/worker-task-001/result.json" in result["command"]

    def test_claude_cli_contains_prompt_file(self, models_file):
        result = generate_spawn_command(
            model="deepseek-v4-pro", engine="claude-cli", **_COMMON
        )
        assert _COMMON["prompt_file"] in result["command"]

    def test_codex_returns_bash_type(self):
        result = generate_spawn_command(
            model="gpt-5.5", engine="codex", **_COMMON
        )
        assert result["type"] == "bash"

    def test_codex_contains_exec(self):
        result = generate_spawn_command(
            model="gpt-5.5", engine="codex", **_COMMON
        )
        assert "codex exec" in result["command"]

    def test_codex_contains_model(self):
        result = generate_spawn_command(
            model="gpt-5.5", engine="codex", **_COMMON
        )
        assert "-m gpt-5.5" in result["command"]

    def test_codex_contains_sandbox(self):
        result = generate_spawn_command(
            model="gpt-5.5", engine="codex", **_COMMON
        )
        assert "--sandbox workspace-write" in result["command"]

    def test_codex_contains_writable_dirs(self):
        result = generate_spawn_command(
            model="gpt-5.5", engine="codex", **_COMMON
        )
        cmd = result["command"]
        assert "--writable-dirs" in cmd
        assert "/tmp/rt/mailbox" in cmd
        assert "/tmp/rt/board" in cmd

    def test_unknown_engine_treated_as_cli(self, models_file):
        result = generate_spawn_command(
            model="some-model", engine="some-other", **_COMMON
        )
        assert result["type"] == "bash"
        assert "--model some-model" in result["command"]


# ---------------------------------------------------------------------------
# TestGenerateSpawnCommandWithEnvVars
# ---------------------------------------------------------------------------

class TestGenerateSpawnCommandWithEnvVars:

    def _save_model(self, models_file, base_url=None, api_key=None):
        entry = {"engine": "claude-cli", "model_id": "my-model", "status": "configured"}
        if base_url:
            entry["base_url"] = base_url
        if api_key:
            entry["api_key"] = api_key
        save_models({"my-model": entry})

    def test_cli_includes_env_vars(self, models_file):
        self._save_model(models_file, base_url="https://api.example.com", api_key="sk-test123")
        result = generate_spawn_command(
            model="my-model", engine="claude-cli", **_COMMON
        )
        cmd = result["command"]
        assert "OPENAI_BASE_URL=https://api.example.com" in cmd
        assert "OPENAI_API_KEY=sk-test123" in cmd

    def test_cli_only_base_url(self, models_file):
        self._save_model(models_file, base_url="https://api.example.com")
        result = generate_spawn_command(
            model="my-model", engine="claude-cli", **_COMMON
        )
        cmd = result["command"]
        assert "OPENAI_BASE_URL=https://api.example.com" in cmd
        assert "OPENAI_API_KEY" not in cmd

    def test_agent_ignores_env_vars(self, models_file):
        self._save_model(models_file, base_url="https://api.example.com", api_key="sk-test123")
        result = generate_spawn_command(
            model="claude-sonnet", engine="claude-agent", **_COMMON
        )
        # Agent commands return a dict, not a string — no env vars injected
        assert isinstance(result["command"], dict)
        cmd_str = json.dumps(result["command"])
        assert "OPENAI_BASE_URL" not in cmd_str
        assert "OPENAI_API_KEY" not in cmd_str


# ---------------------------------------------------------------------------
# TestGetModelEnv
# ---------------------------------------------------------------------------

class TestGetModelEnv:

    def test_model_with_both_vars(self, models_file):
        save_models({"m1": {"base_url": "https://api.example.com", "api_key": "sk-abc"}})
        env = get_model_env("m1")
        assert env == {"OPENAI_BASE_URL": "https://api.example.com", "OPENAI_API_KEY": "sk-abc"}

    def test_model_with_only_base_url(self, models_file):
        save_models({"m2": {"base_url": "https://api.example.com"}})
        env = get_model_env("m2")
        assert env == {"OPENAI_BASE_URL": "https://api.example.com"}
        assert "OPENAI_API_KEY" not in env

    def test_model_not_found(self, models_file):
        save_models({"other": {"base_url": "https://x.com"}})
        env = get_model_env("nonexistent")
        assert env == {}

    def test_empty_models_file(self, models_file):
        # No models.json exists at all
        env = get_model_env("anything")
        assert env == {}


# ---------------------------------------------------------------------------
# TestSpawnCommandIntegration
# ---------------------------------------------------------------------------

class TestSpawnCommandIntegration:

    def test_write_task_then_spawn_command(self, ws):
        task = create_task(
            title="Test CLI",
            description="Integration test",
            engine="claude-cli",
            model="deepseek-v4-pro",
        )
        ws.write_task(task)
        loaded = ws.read_task(task.task_id)
        result = generate_spawn_command(
            task_id=loaded.task_id,
            worker_id=f"worker-{loaded.task_id}",
            model=loaded.model,
            engine=loaded.engine,
            worktree_path="/tmp/wt",
            runtime_dir=str(ws.runtime_dir),
            prompt_file=f"{ws.runtime_dir}/prompts/worker-{loaded.task_id}.md",
        )
        assert result["type"] == "bash"
        assert "--model deepseek-v4-pro" in result["command"]

    def test_write_task_then_spawn_codex(self, ws):
        task = create_task(
            title="Test Codex",
            description="Integration test",
            engine="codex",
            model="gpt-5.5",
        )
        ws.write_task(task)
        loaded = ws.read_task(task.task_id)
        result = generate_spawn_command(
            task_id=loaded.task_id,
            worker_id=f"worker-{loaded.task_id}",
            model=loaded.model,
            engine=loaded.engine,
            worktree_path="/tmp/wt",
            runtime_dir=str(ws.runtime_dir),
            prompt_file=f"{ws.runtime_dir}/prompts/worker-{loaded.task_id}.md",
        )
        assert result["type"] == "bash"
        assert "codex exec" in result["command"]

    def test_write_task_then_spawn_agent(self, ws):
        task = create_task(
            title="Test Agent",
            description="Integration test",
            engine="claude-agent",
            model="sonnet",
        )
        ws.write_task(task)
        loaded = ws.read_task(task.task_id)
        result = generate_spawn_command(
            task_id=loaded.task_id,
            worker_id=f"worker-{loaded.task_id}",
            model=loaded.model,
            engine=loaded.engine,
            worktree_path="/tmp/wt",
            runtime_dir=str(ws.runtime_dir),
            prompt_file=f"{ws.runtime_dir}/prompts/worker-{loaded.task_id}.md",
        )
        assert result["type"] == "agent"
        assert isinstance(result["command"], dict)
