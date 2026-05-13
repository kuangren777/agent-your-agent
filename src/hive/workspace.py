"""
.hive/ directory management and file I/O.

Usage as CLI tool (called by PM via Bash):
    python3 -m hive.workspace init [--pm-session] [--name NAME]
    python3 -m hive.workspace list-pms
    python3 -m hive.workspace write-task '{"task_id":"...","title":"...",...}'
    python3 -m hive.workspace update-task TASK_ID '{"status":"done"}'
    python3 -m hive.workspace send-msg '{"from_agent":"pm","to_agent":"w-0",...}'
    python3 -m hive.workspace read-inbox AGENT_ID
    python3 -m hive.workspace log-event '{"actor":"pm","event_type":"task.created",...}'
    python3 -m hive.workspace status
    python3 -m hive.workspace list-tasks [--pm PM_ID]
    python3 -m hive.workspace check-file-conflicts TASK_ID
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from hive.models import (
    Event,
    HiveState,
    Message,
    PMSession,
    TaskSpec,
    create_pm_session,
    _now_iso,
)

HIVE_DIR_NAME = ".hive"
REGISTRY_PATH = Path.home() / ".hive-registry.json"

SUBDIRS = [
    "tasks",
    "pms",
    "board",
    "checkpoints",
    "worktrees",
    "logs",
]


class Workspace:
    def __init__(self, project_dir: str = "."):
        self.project_dir = Path(project_dir).resolve()
        self.hive_dir = self.project_dir / HIVE_DIR_NAME

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    def exists(self) -> bool:
        return self.hive_dir.is_dir()

    def init(self, project_name: Optional[str] = None) -> HiveState:
        name = project_name or self.project_dir.name
        for d in SUBDIRS:
            (self.hive_dir / d).mkdir(parents=True, exist_ok=True)
        # mailbox root (per-pm dirs created on register_pm)
        (self.hive_dir / "mailbox").mkdir(exist_ok=True)

        if (self.hive_dir / "state.json").exists():
            return self.load_state()

        state = HiveState(project_name=name, started_at=_now_iso())
        self._write_json(self.hive_dir / "state.json", state.to_dict())

        # default config with model registry
        if not (self.hive_dir / "config.json").exists():
            self._write_json(self.hive_dir / "config.json", _default_config())

        # events.jsonl (touch)
        (self.hive_dir / "events.jsonl").touch()

        return state

    # ------------------------------------------------------------------
    # PM Session
    # ------------------------------------------------------------------

    def register_pm(self, task: str) -> PMSession:
        pm = create_pm_session(task)
        self._write_json(self.hive_dir / "pms" / f"{pm.id}.json", pm.to_dict())
        (self.hive_dir / "mailbox" / pm.id).mkdir(parents=True, exist_ok=True)

        # update state
        state = self.load_state()
        state.pm_sessions.append(pm.id)
        self.save_state(state)

        # update global registry
        self._update_registry(pm)

        return pm

    def list_pms(self) -> List[PMSession]:
        pms_dir = self.hive_dir / "pms"
        if not pms_dir.exists():
            return []
        result = []
        for f in sorted(pms_dir.glob("pm-*.json")):
            result.append(PMSession.from_dict(self._read_json(f)))
        return result

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def load_state(self) -> HiveState:
        return HiveState.from_dict(self._read_json(self.hive_dir / "state.json"))

    def save_state(self, state: HiveState) -> None:
        self._write_json_atomic(self.hive_dir / "state.json", state.to_dict())

    def load_config(self) -> Dict[str, Any]:
        return self._read_json(self.hive_dir / "config.json")

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def write_task(self, task: TaskSpec) -> None:
        task.updated_at = _now_iso()
        self._write_json(
            self.hive_dir / "tasks" / f"{task.task_id}.json", task.to_dict()
        )

    def read_task(self, task_id: str) -> TaskSpec:
        return TaskSpec.from_dict(
            self._read_json(self.hive_dir / "tasks" / f"{task_id}.json")
        )

    def update_task(self, task_id: str, patch: Dict[str, Any]) -> TaskSpec:
        task = self.read_task(task_id)
        for k, v in patch.items():
            if hasattr(task, k):
                setattr(task, k, v)
        task.updated_at = _now_iso()
        self.write_task(task)
        return task

    def list_tasks(self, pm_session: Optional[str] = None) -> List[TaskSpec]:
        tasks_dir = self.hive_dir / "tasks"
        if not tasks_dir.exists():
            return []
        result = []
        for f in sorted(tasks_dir.glob("task-*.json")):
            t = TaskSpec.from_dict(self._read_json(f))
            if pm_session and t.pm_session != pm_session:
                continue
            result.append(t)
        return result

    def check_file_conflicts(self, task_id: str) -> List[str]:
        task = self.read_task(task_id)
        owned = set(task.owned_files)
        conflicts = []
        for other in self.list_tasks():
            if other.task_id == task_id:
                continue
            if other.status in ("assigned", "in_progress"):
                overlap = owned & set(other.owned_files)
                if overlap:
                    conflicts.append(
                        f"{other.task_id} ({other.assigned_to}): {sorted(overlap)}"
                    )
        return conflicts

    # ------------------------------------------------------------------
    # Mailbox
    # ------------------------------------------------------------------

    def send_message(self, msg: Message) -> None:
        inbox = self.hive_dir / "mailbox" / msg.to_agent
        inbox.mkdir(parents=True, exist_ok=True)
        self._write_json(inbox / msg.filename, msg.to_dict())

    def read_inbox(self, agent_id: str) -> List[Message]:
        inbox = self.hive_dir / "mailbox" / agent_id
        if not inbox.exists():
            return []
        msgs = []
        for f in sorted(inbox.glob("*.json")):
            msgs.append(Message.from_dict(self._read_json(f)))
        return msgs

    def clear_inbox(self, agent_id: str) -> int:
        inbox = self.hive_dir / "mailbox" / agent_id
        if not inbox.exists():
            return 0
        count = 0
        for f in inbox.glob("*.json"):
            f.unlink()
            count += 1
        return count

    # ------------------------------------------------------------------
    # Event log
    # ------------------------------------------------------------------

    def append_event(self, event: Event) -> None:
        path = self.hive_dir / "events.jsonl"
        line = event.to_json_line() + "\n"
        with open(path, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(line)
            fcntl.flock(f, fcntl.LOCK_UN)

    def log_event(
        self, actor: str, event_type: str, data: Optional[Dict[str, Any]] = None
    ) -> Event:
        seq = self._next_event_seq()
        ev = Event(seq=seq, ts=_now_iso(), actor=actor, event_type=event_type, data=data or {})
        self.append_event(ev)
        return ev

    def read_events(self, n: int = 20) -> List[Event]:
        path = self.hive_dir / "events.jsonl"
        if not path.exists():
            return []
        lines = path.read_text().strip().splitlines()
        return [Event.from_json_line(l) for l in lines[-n:]]

    # ------------------------------------------------------------------
    # Worktree helpers
    # ------------------------------------------------------------------

    def ensure_agent_dirs(self, pm_id: str, agent_id: str) -> None:
        mailbox_id = f"{pm_id}--{agent_id}" if pm_id else agent_id
        (self.hive_dir / "mailbox" / mailbox_id).mkdir(parents=True, exist_ok=True)
        (self.hive_dir / "logs" / agent_id).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Status display
    # ------------------------------------------------------------------

    def status_table(self) -> str:
        state = self.load_state()
        tasks = self.list_tasks()
        pms = self.list_pms()

        lines = [f"Project: {state.project_name}  Status: {state.status}"]
        lines.append(f"Total cost: ${state.total_cost_usd:.2f}")
        lines.append("")

        if pms:
            lines.append("PM Sessions:")
            for pm in pms:
                lines.append(f"  {pm.id}  {pm.status:10s}  {pm.task[:50]}")
            lines.append("")

        if tasks:
            lines.append("Tasks:")
            lines.append(f"  {'ID':12s} {'Status':12s} {'Model':18s} {'Assigned':10s} Title")
            lines.append("  " + "-" * 75)
            for t in tasks:
                assigned = t.assigned_to or "-"
                lines.append(
                    f"  {t.task_id:12s} {t.status:12s} {t.model:18s} {assigned:10s} {t.title[:40]}"
                )
        else:
            lines.append("No tasks.")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_json(self, path: Path) -> Dict[str, Any]:
        return json.loads(path.read_text())

    def _write_json(self, path: Path, data: Dict[str, Any]) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    def _write_json_atomic(self, path: Path, data: Dict[str, Any]) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        os.replace(str(tmp), str(path))

    def _next_event_seq(self) -> int:
        path = self.hive_dir / "events.jsonl"
        if not path.exists() or path.stat().st_size == 0:
            return 1
        lines = path.read_text().strip().splitlines()
        if not lines:
            return 1
        last = json.loads(lines[-1])
        return last.get("seq", 0) + 1

    def _update_registry(self, pm: PMSession) -> None:
        registry = {}  # type: Dict[str, Any]
        if REGISTRY_PATH.exists():
            try:
                registry = json.loads(REGISTRY_PATH.read_text())
            except (json.JSONDecodeError, OSError):
                registry = {}

        proj_key = str(self.project_dir)
        if proj_key not in registry:
            registry[proj_key] = {"pms": {}}
        registry[proj_key]["pms"][pm.id] = {
            "started": pm.started_at,
            "task": pm.task,
            "status": pm.status,
        }
        REGISTRY_PATH.write_text(
            json.dumps(registry, ensure_ascii=False, indent=2) + "\n"
        )


# ---------------------------------------------------------------------------
# Default config with model registry
# ---------------------------------------------------------------------------

def _default_config() -> Dict[str, Any]:
    return {
        "models": {
            "claude-opus": {
                "engine": "claude-agent",
                "model_id": "opus",
                "capabilities": ["architecture", "complex_refactor", "debugging", "multi_file"],
                "swe_bench_verified": 87.6,
                "cost_input_per_mtok": 5.0,
                "cost_output_per_mtok": 25.0,
                "speed": "slow",
                "context_window": 1000000,
                "file_access": "full",
            },
            "claude-sonnet": {
                "engine": "claude-agent",
                "model_id": "sonnet",
                "capabilities": ["implementation", "review", "standard_coding"],
                "swe_bench_verified": 79.6,
                "cost_input_per_mtok": 3.0,
                "cost_output_per_mtok": 15.0,
                "speed": "fast",
                "context_window": 1000000,
                "file_access": "full",
            },
            "claude-haiku": {
                "engine": "claude-agent",
                "model_id": "haiku",
                "capabilities": ["classification", "simple_edit", "formatting", "routing"],
                "swe_bench_verified": 55.0,
                "cost_input_per_mtok": 1.0,
                "cost_output_per_mtok": 5.0,
                "speed": "fastest",
                "context_window": 200000,
                "file_access": "full",
            },
            "deepseek-v4-pro": {
                "engine": "claude-cli",
                "model_id": "deepseek-v4-pro",
                "capabilities": ["implementation", "algorithm", "math", "coding", "boilerplate"],
                "swe_bench_verified": 80.6,
                "cost_input_per_mtok": 1.74,
                "cost_output_per_mtok": 3.48,
                "speed": "fast",
                "context_window": 1000000,
                "file_access": "full",
            },
            "gpt-5.5": {
                "engine": "codex",
                "model_id": "gpt-5.5",
                "capabilities": ["implementation", "testing", "boilerplate", "documentation", "agentic"],
                "swe_bench_verified": 83.0,
                "cost_input_per_mtok": 5.0,
                "cost_output_per_mtok": 30.0,
                "speed": "medium",
                "context_window": 1000000,
                "file_access": "sandbox_write",
            },
        },
        "routing_rules": [
            {"task_type": "architecture", "prefer": "claude-opus", "fallback": "claude-sonnet"},
            {"task_type": "complex_refactor", "prefer": "claude-opus", "fallback": "deepseek-v4-pro"},
            {"task_type": "implementation", "prefer": "deepseek-v4-pro", "fallback": "claude-sonnet"},
            {"task_type": "testing", "prefer": "deepseek-v4-pro", "fallback": "claude-sonnet"},
            {"task_type": "boilerplate", "prefer": "deepseek-v4-pro", "fallback": "gpt-5.5"},
            {"task_type": "review", "prefer": "claude-sonnet", "fallback": "claude-haiku"},
            {"task_type": "documentation", "prefer": "gpt-5.5", "fallback": "claude-haiku"},
            {"task_type": "debugging", "prefer": "claude-opus", "fallback": "claude-sonnet"},
        ],
        "engine_configs": {
            "claude-agent": {
                "spawn_via": "Agent tool",
                "permission_mode": "bypassPermissions",
            },
            "claude-cli": {
                "spawn_via": "claude -p --model {model_id} --output-format json --permission-mode bypassPermissions",
            },
            "codex": {
                "spawn_via": "codex exec -m {model_id} --sandbox workspace-write --cd {worktree}",
                "extra_flags": "--writable-dirs {mailbox_path} {board_path}",
            },
        },
    }


# ---------------------------------------------------------------------------
# CLI entry point: python3 -m hive.workspace <command> [args...]
# ---------------------------------------------------------------------------

def _cli_main() -> None:
    args = sys.argv[1:]
    if not args:
        print("Usage: python3 -m hive.workspace <command> [args]")
        print("Commands: init, list-pms, write-task, update-task, send-msg,")
        print("          read-inbox, log-event, status, list-tasks, check-file-conflicts")
        sys.exit(1)

    cmd = args[0]
    ws = Workspace(".")

    if cmd == "init":
        pm_session = "--pm-session" in args
        name = None
        for i, a in enumerate(args):
            if a == "--name" and i + 1 < len(args):
                name = args[i + 1]
        state = ws.init(name)
        print(f"Initialized .hive/ for project '{state.project_name}'")
        if pm_session:
            task_desc = ""
            for i, a in enumerate(args):
                if a == "--task" and i + 1 < len(args):
                    task_desc = args[i + 1]
            pm = ws.register_pm(task_desc or "default")
            print(f"PM Session: {pm.id}")

    elif cmd == "list-pms":
        for pm in ws.list_pms():
            print(json.dumps(pm.to_dict(), ensure_ascii=False))

    elif cmd == "write-task":
        data = json.loads(args[1])
        task = TaskSpec.from_dict(data)
        ws.write_task(task)
        print(f"Wrote task {task.task_id}")

    elif cmd == "update-task":
        task_id = args[1]
        patch = json.loads(args[2])
        task = ws.update_task(task_id, patch)
        print(f"Updated {task.task_id}: {patch}")

    elif cmd == "send-msg":
        data = json.loads(args[1])
        msg = Message.from_dict(data)
        ws.send_message(msg)
        print(f"Sent {msg.msg_type} from {msg.from_agent} to {msg.to_agent}")

    elif cmd == "read-inbox":
        agent_id = args[1]
        msgs = ws.read_inbox(agent_id)
        for m in msgs:
            print(json.dumps(m.to_dict(), ensure_ascii=False))

    elif cmd == "log-event":
        data = json.loads(args[1])
        ev = ws.log_event(
            actor=data.get("actor", "unknown"),
            event_type=data.get("event_type", "unknown"),
            data=data.get("data", {}),
        )
        print(f"Event #{ev.seq}: {ev.event_type}")

    elif cmd == "status":
        print(ws.status_table())

    elif cmd == "list-tasks":
        pm_id = None
        for i, a in enumerate(args):
            if a == "--pm" and i + 1 < len(args):
                pm_id = args[i + 1]
        for t in ws.list_tasks(pm_id):
            print(json.dumps(t.to_dict(), ensure_ascii=False))

    elif cmd == "check-file-conflicts":
        task_id = args[1]
        conflicts = ws.check_file_conflicts(task_id)
        if conflicts:
            print("CONFLICTS:")
            for c in conflicts:
                print(f"  {c}")
        else:
            print("No file conflicts.")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    _cli_main()
