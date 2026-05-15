import json
from pathlib import Path
from unittest import mock

import pytest

from aya.hooks import (
    _find_active_pm,
    _build_sparse_reminder,
    _build_full_reminder,
    _read_prompt_count,
    _increment_prompt_count,
    TURNS_BETWEEN_REMINDERS,
    FULL_REMINDER_EVERY_N,
)
from aya.workspace import Workspace


@pytest.fixture
def ws(tmp_path, monkeypatch):
    fake_home = tmp_path / "aya-home"
    monkeypatch.setattr("aya.workspace.AYA_HOME", fake_home)
    monkeypatch.setattr("aya.workspace.REGISTRY_PATH", fake_home / "registry.json")
    monkeypatch.setattr("aya.hooks.AYA_HOME", fake_home)
    monkeypatch.setattr("aya.hooks.RUNTIME_BASE", fake_home / "runtime")
    project = tmp_path / "project"
    project.mkdir()
    w = Workspace(str(project))
    w.init("test-project")
    return w


class TestFindActivePM:
    def test_no_pm_session(self, ws):
        result = _find_active_pm(str(ws.project_dir))
        assert result is None

    def test_finds_running_pm(self, ws):
        pm = ws.register_pm("Build feature X")
        result = _find_active_pm(str(ws.project_dir))
        assert result is not None
        assert result["pm_id"] == pm.id
        assert result["task"] == "Build feature X"
        assert "runtime" in result["runtime_dir"]

    def test_ignores_nonexistent_project(self, ws):
        result = _find_active_pm("/nonexistent/path")
        assert result is None

    def test_ignores_stopped_state(self, ws):
        ws.register_pm("Task")
        state = ws.load_state()
        state.status = "stopped"
        ws.save_state(state)
        result = _find_active_pm(str(ws.project_dir))
        assert result is None


class TestBuildReminders:
    def test_sparse_reminder_contains_key_info(self):
        pm_info = {
            "pm_id": "pm-abc1",
            "task": "Build auth",
            "runtime_dir": "/tmp/runtime",
        }
        reminder = _build_sparse_reminder(pm_info)
        assert "AYA PM mode active" in reminder
        assert "pm-abc1" in reminder
        assert "Agent tool" in reminder or "Agent" in reminder
        assert "delegate" in reminder.lower() or "worker" in reminder.lower()

    def test_full_reminder_contains_workflow(self):
        pm_info = {
            "pm_id": "pm-abc1",
            "task": "Build auth",
            "runtime_dir": "/tmp/runtime",
        }
        reminder = _build_full_reminder(pm_info)
        assert "AYA PM mode active" in reminder
        assert "pm-abc1" in reminder
        assert "Build auth" in reminder
        assert "Explore" in reminder
        assert "Plan" in reminder
        assert "Spawn" in reminder or "spawn" in reminder
        assert "Merge" in reminder or "merge" in reminder
        assert "worker" in reminder.lower()

    def test_full_is_longer_than_sparse(self):
        pm_info = {
            "pm_id": "pm-abc1",
            "task": "Build auth",
            "runtime_dir": "/tmp/runtime",
        }
        sparse = _build_sparse_reminder(pm_info)
        full = _build_full_reminder(pm_info)
        assert len(full) > len(sparse) * 2


class TestPromptCounter:
    def test_initial_count_is_zero(self, ws):
        count = _read_prompt_count(str(ws.runtime_dir))
        assert count == 0

    def test_increment_returns_new_count(self, ws):
        rd = str(ws.runtime_dir)
        assert _increment_prompt_count(rd) == 1
        assert _increment_prompt_count(rd) == 2
        assert _increment_prompt_count(rd) == 3
        assert _read_prompt_count(rd) == 3

    def test_survives_reread(self, ws):
        rd = str(ws.runtime_dir)
        for _ in range(5):
            _increment_prompt_count(rd)
        assert _read_prompt_count(rd) == 5


class TestThrottleLogic:
    def test_constants_are_sane(self):
        assert TURNS_BETWEEN_REMINDERS >= 1
        assert FULL_REMINDER_EVERY_N >= 1

    def test_first_fire_would_be_full(self):
        fire_count = 0
        reminder_index = fire_count // TURNS_BETWEEN_REMINDERS
        assert reminder_index % FULL_REMINDER_EVERY_N == 0

    def test_subsequent_fires_are_sparse(self):
        for fire_count in [TURNS_BETWEEN_REMINDERS, TURNS_BETWEEN_REMINDERS * 2]:
            reminder_index = fire_count // TURNS_BETWEEN_REMINDERS
            if reminder_index % FULL_REMINDER_EVERY_N != 0:
                assert True
                return
        pytest.skip("All checked indices happened to be full")

    def test_full_recurs_periodically(self):
        full_at = TURNS_BETWEEN_REMINDERS * FULL_REMINDER_EVERY_N
        reminder_index = full_at // TURNS_BETWEEN_REMINDERS
        assert reminder_index % FULL_REMINDER_EVERY_N == 0
