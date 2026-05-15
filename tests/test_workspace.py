import json
import os
from pathlib import Path
from unittest import mock

import pytest

from aya.models import TaskSpec, create_message, create_task
from aya.workspace import Workspace, AYA_HOME


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
    return w


class TestInit:
    def test_creates_runtime_dirs(self, ws):
        rt = ws.runtime_dir
        assert rt.is_dir()
        assert (rt / "tasks").is_dir()
        assert (rt / "pms").is_dir()
        assert (rt / "board").is_dir()
        assert (rt / "mailbox").is_dir()
        assert (rt / "logs").is_dir()

    def test_runtime_is_outside_repo(self, ws):
        assert not str(ws.runtime_dir).startswith(str(ws.project_dir))

    def test_creates_symlink(self, ws):
        link = ws.project_dir / ".aya"
        assert link.is_symlink() or link.is_dir()

    def test_creates_state(self, ws):
        state = ws.load_state()
        assert state.project_name == "test-project"
        assert state.status == "running"
        assert state.version == "0.1.0"

    def test_creates_config(self, ws):
        config = ws.load_config()
        assert "models" in config
        assert "claude-opus" in config["models"]
        assert "deepseek-v4-pro" in config["models"]
        assert "routing_rules" in config

    def test_creates_events_file(self, ws):
        assert (ws.runtime_dir / "events.jsonl").exists()

    def test_creates_project_json(self, ws):
        pj = json.loads((ws.runtime_dir / "project.json").read_text())
        assert pj["project_dir"] == str(ws.project_dir)

    def test_idempotent(self, ws):
        ws.init("test-project")
        state = ws.load_state()
        assert state.project_name == "test-project"


class TestPMSession:
    def test_register_pm(self, ws):
        pm = ws.register_pm("Build feature A")
        assert pm.id.startswith("pm-")
        assert (ws.runtime_dir / "pms" / f"{pm.id}.json").exists()
        assert (ws.runtime_dir / "mailbox" / pm.id).is_dir()

        state = ws.load_state()
        assert pm.id in state.pm_sessions

    def test_list_pms(self, ws):
        pm1 = ws.register_pm("Feature A")
        pm2 = ws.register_pm("Feature B")
        pms = ws.list_pms()
        ids = [p.id for p in pms]
        assert pm1.id in ids
        assert pm2.id in ids

    def test_updates_registry(self, ws, monkeypatch):
        import aya.workspace as mod
        reg_path = mod.REGISTRY_PATH
        pm = ws.register_pm("Test task")
        assert reg_path.exists()
        reg = json.loads(reg_path.read_text())
        proj_key = str(ws.project_dir)
        assert proj_key in reg
        assert pm.id in reg[proj_key]["pms"]


class TestTasks:
    def test_write_and_read(self, ws):
        t = create_task("Auth", "Implement auth", model="opus")
        ws.write_task(t)
        t2 = ws.read_task(t.task_id)
        assert t2.title == "Auth"
        assert t2.model == "opus"

    def test_list_tasks(self, ws):
        t1 = create_task("Task A", "desc A", pm_session="pm-1111")
        t2 = create_task("Task B", "desc B", pm_session="pm-2222")
        ws.write_task(t1)
        ws.write_task(t2)

        all_tasks = ws.list_tasks()
        assert len(all_tasks) == 2

        pm1_tasks = ws.list_tasks(pm_session="pm-1111")
        assert len(pm1_tasks) == 1
        assert pm1_tasks[0].task_id == t1.task_id

    def test_update_task(self, ws):
        t = create_task("T", "D")
        ws.write_task(t)
        updated = ws.update_task(t.task_id, {"status": "done", "result": "All good"})
        assert updated.status == "done"
        assert updated.result == "All good"

        reread = ws.read_task(t.task_id)
        assert reread.status == "done"

    def test_file_conflict_detection(self, ws):
        t1 = create_task("A", "D")
        t1.owned_files = ["src/auth.py", "src/utils.py"]
        t1.status = "in_progress"
        t1.assigned_to = "worker-0"
        ws.write_task(t1)

        t2 = create_task("B", "D")
        t2.owned_files = ["src/auth.py", "src/api.py"]
        ws.write_task(t2)

        conflicts = ws.check_file_conflicts(t2.task_id)
        assert len(conflicts) == 1
        assert "src/auth.py" in conflicts[0]

    def test_no_conflict_when_different_files(self, ws):
        t1 = create_task("A", "D")
        t1.owned_files = ["src/auth.py"]
        t1.status = "in_progress"
        ws.write_task(t1)

        t2 = create_task("B", "D")
        t2.owned_files = ["src/api.py"]
        ws.write_task(t2)

        conflicts = ws.check_file_conflicts(t2.task_id)
        assert len(conflicts) == 0


class TestMailbox:
    def test_send_and_read(self, ws):
        msg = create_message("worker-0", "pm", "completion", "Done", data={"x": 1})
        ws.send_message(msg)

        msgs = ws.read_inbox("pm")
        assert len(msgs) == 1
        assert msgs[0].from_agent == "worker-0"
        assert msgs[0].data["x"] == 1

    def test_multiple_messages(self, ws):
        for i in range(3):
            msg = create_message(f"worker-{i}", "pm", "progress", f"Step {i}")
            ws.send_message(msg)

        msgs = ws.read_inbox("pm")
        assert len(msgs) == 3

    def test_clear_inbox(self, ws):
        msg = create_message("w-0", "pm", "done", "OK")
        ws.send_message(msg)
        assert len(ws.read_inbox("pm")) == 1

        cleared = ws.clear_inbox("pm")
        assert cleared == 1
        assert len(ws.read_inbox("pm")) == 0

    def test_separate_inboxes(self, ws):
        m1 = create_message("pm", "worker-0", "assign", "Task 1")
        m2 = create_message("pm", "worker-1", "assign", "Task 2")
        ws.send_message(m1)
        ws.send_message(m2)

        assert len(ws.read_inbox("worker-0")) == 1
        assert len(ws.read_inbox("worker-1")) == 1


class TestEventLog:
    def test_log_and_read(self, ws):
        ws.log_event("pm", "task.created", {"task_id": "t-1"})
        ws.log_event("pm", "agent.spawned", {"agent": "w-0"})

        events = ws.read_events(10)
        assert len(events) == 2
        assert events[0].seq == 1
        assert events[1].seq == 2
        assert events[0].event_type == "task.created"

    def test_seq_increments(self, ws):
        for i in range(5):
            ws.log_event("pm", f"event-{i}")

        events = ws.read_events(10)
        seqs = [e.seq for e in events]
        assert seqs == [1, 2, 3, 4, 5]


class TestAgentDirs:
    def test_ensure_agent_dirs(self, ws):
        ws.ensure_agent_dirs("pm-abc1", "worker-0")
        assert (ws.runtime_dir / "mailbox" / "pm-abc1--worker-0").is_dir()
        assert (ws.runtime_dir / "logs" / "worker-0").is_dir()


class TestWorktrees:
    def test_worktree_path(self, ws):
        wt = ws.worktree_path("worker-T1")
        assert str(wt).startswith(str(ws.project_dir))
        assert ".aya-worktrees" in str(wt)

    def test_list_worktrees_empty(self, ws):
        assert ws.list_worktrees() == []

    def test_cleanup_empty(self, ws):
        assert ws.cleanup_worktrees() == 0


class TestCheckEnv:
    def test_detects_missing_tools(self, ws, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda x: None)
        issues = ws.check_env()
        engines = [i["engine"] for i in issues]
        assert any("claude" in e for e in engines)
        assert any("codex" in e or "GPT" in e for e in engines)

    def test_all_ok(self, ws, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda x: f"/usr/bin/{x}")
        issues = ws.check_env()
        assert issues == []


class TestRouteModel:
    def test_known_task_type(self, ws, monkeypatch):
        from aya.workspace import route_model
        monkeypatch.chdir(ws.project_dir)
        result = route_model("implementation")
        assert result["model"] == "deepseek-v4-pro"
        assert result["engine"] == "claude-cli"
        assert "fallback" in result

    def test_unknown_task_type_falls_back(self, ws, monkeypatch):
        from aya.workspace import route_model
        monkeypatch.chdir(ws.project_dir)
        result = route_model("unknown_type")
        assert result["model"] == "claude-sonnet"

    def test_architecture_routes_to_opus(self, ws, monkeypatch):
        from aya.workspace import route_model
        monkeypatch.chdir(ws.project_dir)
        result = route_model("architecture")
        assert result["model"] == "claude-opus"

    def test_testing_routes_to_gpt(self, ws, monkeypatch):
        from aya.workspace import route_model
        monkeypatch.chdir(ws.project_dir)
        result = route_model("testing")
        assert result["model"] == "gpt-5.5"


class TestSpawnCommand:
    def test_claude_agent_spawn(self, ws):
        from aya.workspace import generate_spawn_command
        result = generate_spawn_command(
            "task-001", "worker-task-001", "claude-sonnet", "claude-agent",
            "/tmp/wt", str(ws.runtime_dir), "/tmp/prompt.md",
        )
        assert result["type"] == "agent"
        assert result["command"]["model"] == "sonnet"

    def test_claude_cli_spawn(self, ws):
        from aya.workspace import generate_spawn_command
        result = generate_spawn_command(
            "task-002", "worker-task-002", "deepseek-v4-pro", "claude-cli",
            "/tmp/wt", str(ws.runtime_dir), "/tmp/prompt.md",
        )
        assert result["type"] == "bash"
        assert "claude -p" in result["command"]
        assert "deepseek-v4-pro" in result["command"]

    def test_codex_spawn(self, ws):
        from aya.workspace import generate_spawn_command
        result = generate_spawn_command(
            "task-003", "worker-task-003", "gpt-5.5", "codex",
            "/tmp/wt", str(ws.runtime_dir), "/tmp/prompt.md",
        )
        assert result["type"] == "bash"
        assert "codex exec" in result["command"]
        assert "gpt-5.5" in result["command"]


class TestStatusTable:
    def test_status_output(self, ws):
        pm = ws.register_pm("Build X")
        t = create_task("Auth", "Build auth", pm_session=pm.id, model="opus")
        t.status = "in_progress"
        t.assigned_to = "worker-0"
        ws.write_task(t)

        output = ws.status_table()
        assert "test-project" in output
        assert pm.id in output
        assert "Auth" in output
        assert "opus" in output
        assert "Runtime:" in output


class TestListModelsFull:
    def test_returns_all_default_models(self, ws, monkeypatch):
        monkeypatch.chdir(ws.project_dir)
        results = ws.list_models_full()
        names = [m["name"] for m in results]
        assert "claude-opus" in names
        assert "claude-sonnet" in names
        assert "claude-haiku" in names
        assert "deepseek-v4-pro" in names
        assert "gpt-5.5" in names

    def test_each_model_has_required_keys(self, ws, monkeypatch):
        monkeypatch.chdir(ws.project_dir)
        results = ws.list_models_full()
        for model in results:
            assert "name" in model
            assert "engine" in model
            assert "routing_priority" in model
            assert "available" in model

    def test_routing_priority_populated(self, ws, monkeypatch):
        monkeypatch.chdir(ws.project_dir)
        results = ws.list_models_full()
        opus = next(m for m in results if m["name"] == "claude-opus")
        assert "architecture" in opus["routing_priority"]

    def test_deepseek_has_implementation(self, ws, monkeypatch):
        monkeypatch.chdir(ws.project_dir)
        results = ws.list_models_full()
        deepseek = next(m for m in results if m["name"] == "deepseek-v4-pro")
        assert "implementation" in deepseek["routing_priority"]

    def test_availability_all_missing(self, ws, monkeypatch):
        monkeypatch.chdir(ws.project_dir)
        monkeypatch.setattr("shutil.which", lambda x: None)
        results = ws.list_models_full()
        assert all(m["available"] is False for m in results)

    def test_availability_all_present(self, ws, monkeypatch):
        monkeypatch.chdir(ws.project_dir)
        monkeypatch.setattr("shutil.which", lambda x: f"/usr/bin/{x}")
        results = ws.list_models_full()
        assert all(m["available"] is True for m in results)

    def test_user_models_merged(self, ws, monkeypatch, tmp_path):
        import aya.workspace as mod
        fake_models_file = tmp_path / "models.json"
        monkeypatch.setattr(mod, "MODELS_FILE", fake_models_file)
        monkeypatch.chdir(ws.project_dir)
        mod.save_models({"my-custom-model": {"engine": "claude-agent", "model_id": "my-custom-model"}})
        results = ws.list_models_full()
        names = [m["name"] for m in results]
        assert "my-custom-model" in names

    def test_returns_list_of_dicts(self, ws, monkeypatch):
        monkeypatch.chdir(ws.project_dir)
        results = ws.list_models_full()
        assert isinstance(results, list)
        assert len(results) >= 5
        assert all(isinstance(m, dict) for m in results)
