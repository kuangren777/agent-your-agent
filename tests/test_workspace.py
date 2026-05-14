import json
import os
import tempfile
from pathlib import Path

import pytest

from aya.models import TaskSpec, create_message, create_task
from aya.workspace import Workspace


@pytest.fixture
def ws(tmp_path):
    w = Workspace(str(tmp_path))
    w.init("test-project")
    return w


class TestInit:
    def test_creates_dirs(self, ws):
        aya_dir = ws.aya_dir
        assert (aya_dir / "tasks").is_dir()
        assert (aya_dir / "pms").is_dir()
        assert (aya_dir / "board").is_dir()
        assert (aya_dir / "mailbox").is_dir()
        assert (aya_dir / "worktrees").is_dir()
        assert (aya_dir / "logs").is_dir()

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
        assert (ws.aya_dir / "events.jsonl").exists()

    def test_idempotent(self, ws):
        ws.init("test-project")
        state = ws.load_state()
        assert state.project_name == "test-project"


class TestPMSession:
    def test_register_pm(self, ws):
        pm = ws.register_pm("Build feature A")
        assert pm.id.startswith("pm-")
        assert (ws.aya_dir / "pms" / f"{pm.id}.json").exists()
        assert (ws.aya_dir / "mailbox" / pm.id).is_dir()

        state = ws.load_state()
        assert pm.id in state.pm_sessions

    def test_list_pms(self, ws):
        pm1 = ws.register_pm("Feature A")
        pm2 = ws.register_pm("Feature B")
        pms = ws.list_pms()
        ids = [p.id for p in pms]
        assert pm1.id in ids
        assert pm2.id in ids

    def test_updates_registry(self, ws):
        from aya.workspace import REGISTRY_PATH

        pm = ws.register_pm("Test task")
        assert REGISTRY_PATH.exists()
        reg = json.loads(REGISTRY_PATH.read_text())
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
        assert (ws.aya_dir / "mailbox" / "pm-abc1--worker-0").is_dir()
        assert (ws.aya_dir / "logs" / "worker-0").is_dir()


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
