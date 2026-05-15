"""
Integration tests for AYA's dual-mode communication system.

Sub-agent mode: board broadcast + mailbox (fire-and-forget pattern).
Teammate mode: bidirectional mailbox messaging (peer-to-peer pattern).
"""
from __future__ import annotations

import time

import pytest

from aya.models import create_message, create_task
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


# ---------------------------------------------------------------------------
# Sub-agent board protocol
# ---------------------------------------------------------------------------

class TestSubAgentBoardProtocol:
    """Sub-agent pattern: workers write to board/ and mailbox/{pm_id}/, PM reads."""

    def test_worker_writes_interface_to_board(self, ws):
        board_file = ws.runtime_dir / "board" / "interface-task-001.md"
        board_file.write_text("## Public Interface\ndef foo() -> None: ...")
        assert board_file.exists()
        assert "def foo()" in board_file.read_text()

    def test_multiple_workers_write_board_no_conflict(self, ws):
        board = ws.runtime_dir / "board"
        (board / "interface-task-001.md").write_text("# Interface for task-001")
        (board / "interface-task-002.md").write_text("# Interface for task-002")
        assert (board / "interface-task-001.md").exists()
        assert (board / "interface-task-002.md").exists()

    def test_worker_completion_message_structure(self, ws):
        pm = ws.register_pm("Build feature A")
        completion_data = {
            "task_id": "task-001",
            "status": "done",
            "branch": "agent/task-001",
            "files_changed": ["src/foo.py", "tests/test_foo.py"],
            "test_result": "passed",
            "summary": "Implemented foo module with 90% coverage",
        }
        msg = create_message(
            from_agent="worker-001",
            to_agent=pm.id,
            msg_type="completion",
            subject="Task task-001 complete",
            body="All acceptance criteria met.",
            data=completion_data,
        )
        ws.send_message(msg)

        msgs = ws.read_inbox(pm.id)
        assert len(msgs) == 1
        m = msgs[0]
        assert m.data["task_id"] == "task-001"
        assert m.data["status"] == "done"
        assert m.data["branch"] == "agent/task-001"
        assert m.data["files_changed"] == ["src/foo.py", "tests/test_foo.py"]
        assert m.data["test_result"] == "passed"
        assert m.data["summary"] == "Implemented foo module with 90% coverage"

    def test_worker_question_message(self, ws):
        pm = ws.register_pm("Feature Q")
        msg = create_message(
            from_agent="worker-002",
            to_agent=pm.id,
            msg_type="question",
            subject="Clarification needed",
            body="Should I use async or sync?",
        )
        ws.send_message(msg)

        msgs = ws.read_inbox(pm.id)
        assert len(msgs) == 1
        assert msgs[0].from_agent == "worker-002"
        assert msgs[0].msg_type == "question"

    def test_worker_progress_message(self, ws):
        pm = ws.register_pm("Feature P")
        msg = create_message(
            from_agent="worker-003",
            to_agent=pm.id,
            msg_type="progress",
            subject="Step 3 of 5 done",
            data={"percent": 60},
        )
        ws.send_message(msg)

        msgs = ws.read_inbox(pm.id)
        assert len(msgs) == 1
        assert msgs[0].data["percent"] == 60

    def test_pm_reads_multiple_worker_completions(self, ws):
        pm = ws.register_pm("Big feature")
        for i in range(3):
            msg = create_message(
                from_agent=f"worker-{i:03d}",
                to_agent=pm.id,
                msg_type="completion",
                subject=f"Task {i} done",
                data={"task_id": f"task-{i:03d}", "status": "done"},
            )
            ws.send_message(msg)
            # Small delay so filenames differ (timestamp-based)
            time.sleep(0.01)

        msgs = ws.read_inbox(pm.id)
        assert len(msgs) == 3
        from_agents = {m.from_agent for m in msgs}
        assert from_agents == {"worker-000", "worker-001", "worker-002"}

    def test_board_is_read_only_for_workers(self, ws):
        """PM writes requirements.md; worker writes interface file; PM content preserved."""
        board = ws.runtime_dir / "board"
        pm_req = board / "requirements.md"
        pm_req.write_text("# Requirements\n- Implement foo\n- Add tests")

        # Worker writes its own interface file without touching PM's file
        worker_iface = board / "interface-task-001.md"
        worker_iface.write_text("## Exported: def foo(x: int) -> str")

        # PM requirements still intact
        assert "Implement foo" in pm_req.read_text()
        # Worker interface also present
        assert "def foo(x: int)" in worker_iface.read_text()


# ---------------------------------------------------------------------------
# Teammate bidirectional messaging
# ---------------------------------------------------------------------------

class TestTeammateBidirectionalMessaging:
    """Teammate pattern: workers send messages to each other via mailbox."""

    def test_agent_a_sends_to_agent_b(self, ws):
        msg = create_message(
            from_agent="worker-A",
            to_agent="worker-B",
            msg_type="handoff",
            subject="Your turn",
            body="Schema ready, proceed with impl.",
            data={"schema_file": "board/schema.json"},
        )
        ws.send_message(msg)

        b_msgs = ws.read_inbox("worker-B")
        assert len(b_msgs) == 1
        m = b_msgs[0]
        assert m.from_agent == "worker-A"
        assert m.msg_type == "handoff"
        assert m.data["schema_file"] == "board/schema.json"

    def test_bidirectional_exchange(self, ws):
        # A → B
        msg_ab = create_message("worker-A", "worker-B", "handoff", "A→B", data={"step": 1})
        ws.send_message(msg_ab)
        # B → A
        msg_ba = create_message("worker-B", "worker-A", "ack", "B→A", data={"step": 2})
        ws.send_message(msg_ba)

        a_msgs = ws.read_inbox("worker-A")
        b_msgs = ws.read_inbox("worker-B")

        assert len(a_msgs) == 1
        assert a_msgs[0].from_agent == "worker-B"
        assert a_msgs[0].data["step"] == 2

        assert len(b_msgs) == 1
        assert b_msgs[0].from_agent == "worker-A"
        assert b_msgs[0].data["step"] == 1

    def test_multiple_messages_preserved_order(self, ws):
        # Use distinct msg_type per message so filenames don't collide
        # (filename = ts_safe + from_agent + msg_type, second-resolution ts).
        msg_types = [f"step-{i}" for i in range(5)]
        for i, mtype in enumerate(msg_types):
            msg = create_message(
                from_agent="worker-A",
                to_agent="worker-B",
                msg_type=mtype,
                subject=f"Step {i}",
                data={"seq": i},
            )
            ws.send_message(msg)

        msgs = ws.read_inbox("worker-B")
        assert len(msgs) == 5
        seqs = [m.data["seq"] for m in msgs]
        assert seqs == sorted(seqs), "Messages not in chronological order"

    def test_broadcast_via_board(self, ws):
        """PM writes architecture doc; both workers read the same content."""
        arch_file = ws.runtime_dir / "board" / "architecture.md"
        arch_file.write_text("# Architecture\nUse layered approach.")

        # Simulate both workers reading
        content_a = arch_file.read_text()
        content_b = arch_file.read_text()

        assert content_a == content_b
        assert "layered approach" in content_a

    def test_separate_inboxes_isolated(self, ws):
        msg = create_message("worker-A", "worker-B", "ping", "Hello B")
        ws.send_message(msg)

        assert len(ws.read_inbox("worker-B")) == 1
        assert len(ws.read_inbox("worker-A")) == 0

    def test_clear_inbox_removes_all(self, ws):
        # Use distinct msg_type per message to avoid filename collisions
        # (filename = ts_safe + from_agent + msg_type, second-resolution ts).
        for i in range(3):
            msg = create_message("sender", "worker-C", f"info-{i}", f"msg {i}")
            ws.send_message(msg)

        assert len(ws.read_inbox("worker-C")) == 3
        ws.clear_inbox("worker-C")
        assert len(ws.read_inbox("worker-C")) == 0

    def test_ensure_agent_dirs_creates_mailbox(self, ws):
        ws.ensure_agent_dirs("pm-001", "worker-0")
        mailbox_dir = ws.runtime_dir / "mailbox" / "pm-001--worker-0"
        assert mailbox_dir.is_dir()


# ---------------------------------------------------------------------------
# Mixed mode scenario
# ---------------------------------------------------------------------------

class TestMixedModeScenario:
    """Sub-agent and teammate tasks coexist in the same PM session."""

    def test_mixed_tasks_different_files(self, ws):
        sub_task = create_task("Sub-agent task", "Fire-and-forget work")
        sub_task.owned_files = ["src/a.py"]
        sub_task.status = "in_progress"
        ws.write_task(sub_task)

        teammate_task = create_task("Teammate task", "Peer-to-peer work")
        teammate_task.owned_files = ["src/b.py"]
        ws.write_task(teammate_task)

        conflicts = ws.check_file_conflicts(teammate_task.task_id)
        assert len(conflicts) == 0

    def test_mixed_tasks_conflict_detection(self, ws):
        sub_task = create_task("Sub-agent task", "Fire-and-forget work")
        sub_task.owned_files = ["src/shared.py", "src/a.py"]
        sub_task.status = "in_progress"
        ws.write_task(sub_task)

        teammate_task = create_task("Teammate task", "Peer-to-peer work")
        teammate_task.owned_files = ["src/shared.py", "src/b.py"]
        ws.write_task(teammate_task)

        conflicts = ws.check_file_conflicts(teammate_task.task_id)
        assert len(conflicts) == 1
        assert "src/shared.py" in conflicts[0]

    def test_sub_agent_and_teammate_share_board(self, ws):
        board = ws.runtime_dir / "board"
        (board / "sub-agent-output.md").write_text("# Sub-agent result")
        (board / "teammate-output.md").write_text("# Teammate result")
        assert (board / "sub-agent-output.md").exists()
        assert (board / "teammate-output.md").exists()

    def test_completion_messages_to_same_pm(self, ws):
        pm = ws.register_pm("Mixed mode scenario")

        # Sub-agent worker sends completion
        sub_msg = create_message(
            from_agent="sub-agent-worker",
            to_agent=pm.id,
            msg_type="completion",
            subject="Sub-agent done",
            data={"task_id": "task-sub", "status": "done", "mode": "sub-agent"},
        )
        ws.send_message(sub_msg)
        time.sleep(0.01)

        # Teammate worker sends completion
        teammate_msg = create_message(
            from_agent="teammate-worker",
            to_agent=pm.id,
            msg_type="completion",
            subject="Teammate done",
            data={"task_id": "task-tm", "status": "done", "mode": "teammate"},
        )
        ws.send_message(teammate_msg)

        msgs = ws.read_inbox(pm.id)
        assert len(msgs) == 2
        modes = {m.data["mode"] for m in msgs}
        assert modes == {"sub-agent", "teammate"}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestCommunicationEdgeCases:
    def test_empty_mailbox(self, ws):
        msgs = ws.read_inbox("nonexistent-agent")
        assert msgs == []

    def test_message_with_special_characters(self, ws):
        special_subject = 'Unicode: 你好 \n newline, "quotes", \'apostrophe\''
        special_body = "Body with\nnewlines and\ttabs and 🚀 emoji"
        msg = create_message(
            from_agent="worker-X",
            to_agent="worker-Y",
            msg_type="info",
            subject=special_subject,
            body=special_body,
        )
        ws.send_message(msg)

        msgs = ws.read_inbox("worker-Y")
        assert len(msgs) == 1
        assert msgs[0].subject == special_subject
        assert msgs[0].body == special_body

    def test_message_with_empty_data(self, ws):
        msg = create_message(
            from_agent="worker-X",
            to_agent="worker-Y",
            msg_type="ping",
            subject="Ping",
            data={},
        )
        ws.send_message(msg)

        msgs = ws.read_inbox("worker-Y")
        assert len(msgs) == 1
        assert msgs[0].data == {}

    def test_large_message_body(self, ws):
        large_body = "x" * 10_240  # 10 KB
        msg = create_message(
            from_agent="worker-X",
            to_agent="worker-Y",
            msg_type="result",
            subject="Large payload",
            body=large_body,
        )
        ws.send_message(msg)

        msgs = ws.read_inbox("worker-Y")
        assert len(msgs) == 1
        assert len(msgs[0].body) == 10_240
        assert msgs[0].body == large_body

    def test_message_filename_format(self, ws):
        msg = create_message(
            from_agent="worker-abc",
            to_agent="pm-001",
            msg_type="completion",
            subject="Done",
        )
        filename = msg.filename
        # Expected pattern: {ts_safe}-{from_agent}-{msg_type}.json
        assert filename.endswith(".json")
        assert "-worker-abc-" in filename
        assert filename.endswith("-completion.json")
        # ts_safe portion: no colons or dashes from iso timestamp, max 15 chars before first dash
        parts = filename.split("-worker-abc-")
        assert len(parts) == 2
        ts_part = parts[0]
        assert ":" not in ts_part
