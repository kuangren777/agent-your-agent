import json

from aya.models import (
    Event,
    AyaState,
    Message,
    PMSession,
    TaskSpec,
    create_message,
    create_pm_session,
    create_task,
)


class TestTaskSpec:
    def test_create_task(self):
        t = create_task("Implement auth", "Build auth module", model="opus")
        assert t.task_id.startswith("task-")
        assert t.title == "Implement auth"
        assert t.status == "pending"
        assert t.model == "opus"
        assert t.branch == f"agent/{t.task_id}"
        assert t.created_at != ""

    def test_roundtrip(self):
        t = create_task(
            "Test task",
            "desc",
            depends_on=["task-abc"],
            owned_files=["src/a.py"],
            read_files=["src/b.py"],
            acceptance_criteria=["tests pass"],
            engine="claude-cli",
            model="deepseek-v4-pro",
        )
        d = t.to_dict()
        t2 = TaskSpec.from_dict(d)
        assert t2.task_id == t.task_id
        assert t2.depends_on == ["task-abc"]
        assert t2.owned_files == ["src/a.py"]
        assert t2.engine == "claude-cli"
        assert t2.model == "deepseek-v4-pro"

    def test_json_roundtrip(self):
        t = create_task("JSON test", "desc")
        j = t.to_json()
        d = json.loads(j)
        t2 = TaskSpec.from_dict(d)
        assert t2.task_id == t.task_id

    def test_from_dict_ignores_extra_keys(self):
        d = {"task_id": "t-1", "title": "T", "description": "D", "extra_field": 42}
        t = TaskSpec.from_dict(d)
        assert t.task_id == "t-1"


class TestMessage:
    def test_create_message(self):
        m = create_message("worker-0", "pm", "completion", "Done", data={"task_id": "t-1"})
        assert m.from_agent == "worker-0"
        assert m.to_agent == "pm"
        assert m.msg_type == "completion"
        assert m.data["task_id"] == "t-1"
        assert m.id.startswith("msg-")

    def test_filename(self):
        m = create_message("worker-0", "pm", "completion", "Done")
        fn = m.filename
        id_suffix = m.id.replace("msg-", "")[:6]
        assert fn.endswith(f"-worker-0-completion-{id_suffix}.json")

    def test_filename_uniqueness(self):
        """Two messages same second/agent/type get different filenames."""
        m1 = create_message("worker-0", "pm", "completion", "Done")
        m2 = create_message("worker-0", "pm", "completion", "Done")
        assert m1.filename != m2.filename

    def test_roundtrip(self):
        m = create_message("pm", "tl-0", "assign", "Task", body="Do this", data={"x": 1})
        d = m.to_dict()
        m2 = Message.from_dict(d)
        assert m2.id == m.id
        assert m2.body == "Do this"
        assert m2.data == {"x": 1}


class TestEvent:
    def test_json_line_roundtrip(self):
        e = Event(seq=1, ts="2026-05-13T10:00:00Z", actor="pm", event_type="task.created", data={"task_id": "t-1"})
        line = e.to_json_line()
        e2 = Event.from_json_line(line)
        assert e2.seq == 1
        assert e2.actor == "pm"
        assert e2.data["task_id"] == "t-1"


class TestPMSession:
    def test_create(self):
        pm = create_pm_session("Build feature A")
        assert pm.id.startswith("pm-")
        assert len(pm.id) == 7  # "pm-" + 4 hex chars
        assert pm.task == "Build feature A"
        assert pm.status == "running"

    def test_roundtrip(self):
        pm = create_pm_session("Test")
        d = pm.to_dict()
        pm2 = PMSession.from_dict(d)
        assert pm2.id == pm.id


class TestAyaState:
    def test_roundtrip(self):
        s = AyaState(project_name="test", pm_sessions=["pm-abc1"])
        d = s.to_dict()
        s2 = AyaState.from_dict(d)
        assert s2.project_name == "test"
        assert s2.pm_sessions == ["pm-abc1"]
