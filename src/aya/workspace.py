"""
AYA runtime workspace — coordination layer lives OUT of the repo.

Layout:
  ~/.aya/runtime/<project-hash>/    coordination (tasks, mailbox, board, events)
  <project>/.aya-worktrees/         worker git worktrees (cleaned up after completion)
  <project>/                        main repo (PM reads + merges only)
  <project>/.aya                    symlink → runtime dir (convenience)

Usage as CLI:
    python3 -m aya.workspace init [--pm-session] [--name NAME] [--task TASK]
    python3 -m aya.workspace list-pms
    python3 -m aya.workspace list-models
    python3 -m aya.workspace write-task JSON
    python3 -m aya.workspace update-task TASK_ID JSON
    python3 -m aya.workspace send-msg JSON
    python3 -m aya.workspace read-inbox AGENT_ID
    python3 -m aya.workspace log-event JSON
    python3 -m aya.workspace status
    python3 -m aya.workspace list-tasks [--pm PM_ID]
    python3 -m aya.workspace check-file-conflicts TASK_ID
    python3 -m aya.workspace create-worktree WORKER_ID BRANCH
    python3 -m aya.workspace remove-worktree WORKER_ID
    python3 -m aya.workspace cleanup-worktrees
    python3 -m aya.workspace check-env
    python3 -m aya.workspace runtime-dir
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from aya.models import (
    Event,
    AyaState,
    Message,
    PMSession,
    TaskSpec,
    create_pm_session,
    _now_iso,
)

AYA_HOME = Path.home() / ".aya"
WORKTREE_DIR_NAME = ".aya-worktrees"
REGISTRY_PATH = AYA_HOME / "registry.json"

SUBDIRS = [
    "tasks",
    "pms",
    "board",
    "checkpoints",
    "logs",
]


def _project_hash(project_dir: Path) -> str:
    return hashlib.sha256(str(project_dir).encode()).hexdigest()[:12]


class Workspace:
    """
    Coordination state at ~/.aya/runtime/<hash>/, NOT inside the repo.
    Worker worktrees at <project>/.aya-worktrees/<worker-id>/.
    """

    def __init__(self, project_dir: str = "."):
        self.project_dir = Path(project_dir).resolve()
        self._proj_hash = _project_hash(self.project_dir)
        self.runtime_dir = AYA_HOME / "runtime" / self._proj_hash
        self.worktree_dir = self.project_dir / WORKTREE_DIR_NAME

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    def exists(self) -> bool:
        return self.runtime_dir.is_dir()

    def init(self, project_name: Optional[str] = None) -> AyaState:
        name = project_name or self.project_dir.name
        for d in SUBDIRS:
            (self.runtime_dir / d).mkdir(parents=True, exist_ok=True)
        (self.runtime_dir / "mailbox").mkdir(exist_ok=True)

        # symlink .aya → runtime_dir for convenience
        link = self.project_dir / ".aya"
        if not link.exists():
            try:
                link.symlink_to(self.runtime_dir)
            except OSError:
                pass

        if (self.runtime_dir / "state.json").exists():
            return self.load_state()

        state = AyaState(project_name=name, started_at=_now_iso())
        self._write_json(self.runtime_dir / "state.json", state.to_dict())

        if not (self.runtime_dir / "config.json").exists():
            self._write_json(self.runtime_dir / "config.json", _default_config())

        (self.runtime_dir / "events.jsonl").touch()

        self._write_json(
            self.runtime_dir / "project.json",
            {"project_dir": str(self.project_dir), "hash": self._proj_hash},
        )

        return state

    # ------------------------------------------------------------------
    # PM Session
    # ------------------------------------------------------------------

    def register_pm(self, task: str) -> PMSession:
        pm = create_pm_session(task)
        self._write_json(self.runtime_dir / "pms" / f"{pm.id}.json", pm.to_dict())
        (self.runtime_dir / "mailbox" / pm.id).mkdir(parents=True, exist_ok=True)

        state = self.load_state()
        state.pm_sessions.append(pm.id)
        self.save_state(state)

        self._update_registry(pm)
        return pm

    def list_pms(self) -> List[PMSession]:
        pms_dir = self.runtime_dir / "pms"
        if not pms_dir.exists():
            return []
        return [
            PMSession.from_dict(self._read_json(f))
            for f in sorted(pms_dir.glob("pm-*.json"))
        ]

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def load_state(self) -> AyaState:
        return AyaState.from_dict(self._read_json(self.runtime_dir / "state.json"))

    def save_state(self, state: AyaState) -> None:
        self._write_json_atomic(self.runtime_dir / "state.json", state.to_dict())

    def load_config(self) -> Dict[str, Any]:
        return self._read_json(self.runtime_dir / "config.json")

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def write_task(self, task: TaskSpec) -> None:
        task.updated_at = _now_iso()
        self._write_json(
            self.runtime_dir / "tasks" / f"{task.task_id}.json", task.to_dict()
        )

    def read_task(self, task_id: str) -> TaskSpec:
        return TaskSpec.from_dict(
            self._read_json(self.runtime_dir / "tasks" / f"{task_id}.json")
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
        tasks_dir = self.runtime_dir / "tasks"
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
        inbox = self.runtime_dir / "mailbox" / msg.to_agent
        inbox.mkdir(parents=True, exist_ok=True)
        self._write_json(inbox / msg.filename, msg.to_dict())

    def read_inbox(self, agent_id: str) -> List[Message]:
        inbox = self.runtime_dir / "mailbox" / agent_id
        if not inbox.exists():
            return []
        return [
            Message.from_dict(self._read_json(f))
            for f in sorted(inbox.glob("*.json"))
        ]

    def clear_inbox(self, agent_id: str) -> int:
        inbox = self.runtime_dir / "mailbox" / agent_id
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
        path = self.runtime_dir / "events.jsonl"
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
        path = self.runtime_dir / "events.jsonl"
        if not path.exists():
            return []
        lines = path.read_text().strip().splitlines()
        return [Event.from_json_line(l) for l in lines[-n:]]

    # ------------------------------------------------------------------
    # Worktree management — physical isolation per worker
    # ------------------------------------------------------------------

    def create_worktree(self, worker_id: str, branch: str) -> Path:
        wt_path = self.worktree_dir / worker_id
        self.worktree_dir.mkdir(parents=True, exist_ok=True)
        if wt_path.exists():
            shutil.rmtree(wt_path)
        subprocess.run(
            ["git", "worktree", "add", str(wt_path), "-b", branch],
            cwd=str(self.project_dir),
            capture_output=True, text=True, check=True,
        )
        return wt_path

    def remove_worktree(self, worker_id: str) -> None:
        wt_path = self.worktree_dir / worker_id
        if wt_path.exists():
            subprocess.run(
                ["git", "worktree", "remove", str(wt_path), "--force"],
                cwd=str(self.project_dir),
                capture_output=True, text=True,
            )
            if wt_path.exists():
                shutil.rmtree(wt_path)

    def cleanup_worktrees(self) -> int:
        if not self.worktree_dir.exists():
            return 0
        count = 0
        for p in list(self.worktree_dir.iterdir()):
            if p.is_dir():
                subprocess.run(
                    ["git", "worktree", "remove", str(p), "--force"],
                    cwd=str(self.project_dir),
                    capture_output=True, text=True,
                )
                if p.exists():
                    shutil.rmtree(p)
                count += 1
        if self.worktree_dir.exists():
            shutil.rmtree(self.worktree_dir)
        return count

    def prepare_spawn(self, task_id: str, prompt_text: str) -> Dict[str, Any]:
        """One-step spawn preparation: create worktree, write prompt, return spawn command.

        After calling this the PM just needs to paste the returned 'command' into
        Agent() (type=='agent') or Bash(run_in_background=True) (type=='bash').
        If worktree creation fails (e.g. not a git repo in tests), it is skipped
        gracefully and the spawn command is still returned.
        """
        task = self.read_task(task_id)
        worker_id = f"worker-{task_id}"
        branch = f"agent/{task_id}"

        # 1. Create worktree (skip if already exists or not a git repo)
        wt_path = self.worktree_dir / worker_id
        if not wt_path.exists():
            try:
                self.create_worktree(worker_id, branch)
            except subprocess.CalledProcessError:
                # branch may already exist — retry with -B equivalent: delete & recreate
                try:
                    subprocess.run(
                        ["git", "worktree", "add", str(wt_path), "-B", branch],
                        cwd=str(self.project_dir),
                        capture_output=True, text=True, check=True,
                    )
                    self.worktree_dir.mkdir(parents=True, exist_ok=True)
                except subprocess.CalledProcessError:
                    pass  # not a git repo or other error; continue without worktree
            except Exception:
                pass  # skip worktree creation silently

        # 2. Create logs directory and write prompt
        log_dir = self.runtime_dir / "logs" / worker_id
        log_dir.mkdir(parents=True, exist_ok=True)
        prompt_file = log_dir / "prompt.md"
        prompt_file.write_text(prompt_text, encoding="utf-8")

        # 3. Generate and return spawn command
        return generate_spawn_command(
            task_id=task_id,
            worker_id=worker_id,
            model=task.model,
            engine=task.engine,
            worktree_path=str(wt_path),
            runtime_dir=str(self.runtime_dir),
            prompt_file=str(prompt_file),
        )

    def worktree_path(self, worker_id: str) -> Path:
        return self.worktree_dir / worker_id

    def list_worktrees(self) -> List[Dict[str, str]]:
        if not self.worktree_dir.exists():
            return []
        return [
            {"worker_id": p.name, "path": str(p)}
            for p in sorted(self.worktree_dir.iterdir())
            if p.is_dir()
        ]

    # ------------------------------------------------------------------
    # Agent dirs
    # ------------------------------------------------------------------

    def ensure_agent_dirs(self, pm_id: str, agent_id: str) -> None:
        mailbox_id = f"{pm_id}--{agent_id}" if pm_id else agent_id
        (self.runtime_dir / "mailbox" / mailbox_id).mkdir(parents=True, exist_ok=True)
        (self.runtime_dir / "logs" / agent_id).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Environment check
    # ------------------------------------------------------------------

    def check_env(self) -> List[Dict[str, str]]:
        issues = []  # type: List[Dict[str, str]]
        if not shutil.which("claude"):
            issues.append({
                "engine": "claude-agent / claude-cli",
                "status": "MISSING",
                "fix": "Install Claude Code: npm install -g @anthropic-ai/claude-code",
            })
        if not shutil.which("codex"):
            issues.append({
                "engine": "codex (GPT-5.5)",
                "status": "MISSING",
                "fix": "Install Codex CLI: npm install -g @openai/codex",
            })
        if not shutil.which("git"):
            issues.append({
                "engine": "git",
                "status": "MISSING",
                "fix": "Install git",
            })
        return issues

    def list_models_full(self) -> List[Dict[str, Any]]:
        """List all models with engine, routing priority, and availability."""
        config = self.load_config()
        config_models = config.get("models", {})
        user_models = load_models()

        all_models = {**config_models, **user_models}

        routing_map: Dict[str, List[str]] = {}
        for rule in config.get("routing_rules", []):
            preferred = rule.get("prefer", "")
            tt = rule.get("task_type", "")
            if preferred and tt:
                routing_map.setdefault(preferred, []).append(tt)

        engine_binaries = {"claude-agent": "claude", "claude-cli": "claude", "codex": "codex"}

        result = []
        for name, cfg in sorted(all_models.items()):
            engine = cfg.get("engine", _detect_engine(name))
            binary = engine_binaries.get(engine, "claude")
            result.append({
                "name": name,
                "engine": engine,
                "routing_priority": routing_map.get(name, []),
                "available": shutil.which(binary) is not None,
                "model_id": cfg.get("model_id", name),
                "status": cfg.get("status", "configured"),
            })
        return result

    # ------------------------------------------------------------------
    # Status display
    # ------------------------------------------------------------------

    def status_table(self) -> str:
        state = self.load_state()
        tasks = self.list_tasks()
        pms = self.list_pms()
        wts = self.list_worktrees()

        lines = [f"Project: {state.project_name}  Status: {state.status}"]
        lines.append(f"Repo:    {self.project_dir}")
        lines.append(f"Runtime: {self.runtime_dir}")
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

        if wts:
            lines.append("")
            lines.append("Worktrees:")
            for wt in wts:
                lines.append(f"  {wt['worker_id']:20s} {wt['path']}")

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
        path = self.runtime_dir / "events.jsonl"
        if not path.exists() or path.stat().st_size == 0:
            return 1
        lines = path.read_text().strip().splitlines()
        if not lines:
            return 1
        last = json.loads(lines[-1])
        return last.get("seq", 0) + 1

    def _update_registry(self, pm: PMSession) -> None:
        REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        registry = {}  # type: Dict[str, Any]
        if REGISTRY_PATH.exists():
            try:
                registry = json.loads(REGISTRY_PATH.read_text())
            except (json.JSONDecodeError, OSError):
                registry = {}

        proj_key = str(self.project_dir)
        if proj_key not in registry:
            registry[proj_key] = {"pms": {}, "runtime": str(self.runtime_dir)}
        registry[proj_key]["pms"][pm.id] = {
            "started": pm.started_at,
            "task": pm.task,
            "status": pm.status,
        }
        REGISTRY_PATH.write_text(
            json.dumps(registry, ensure_ascii=False, indent=2) + "\n"
        )


# ---------------------------------------------------------------------------
# Model setup
# ---------------------------------------------------------------------------

MODELS_FILE = AYA_HOME / "models.json"

ENGINE_RULES = {
    "gpt": "codex",
    "o1": "codex",
    "o3": "codex",
    "o4": "codex",
    "claude": "claude-agent",
    "opus": "claude-agent",
    "sonnet": "claude-agent",
    "haiku": "claude-agent",
}


def _detect_engine(model_name: str) -> str:
    name_lower = model_name.lower()
    for prefix, engine in ENGINE_RULES.items():
        if prefix in name_lower:
            return engine
    return "claude-cli"


def load_models() -> Dict[str, Any]:
    if MODELS_FILE.exists():
        try:
            return json.loads(MODELS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_models(models: Dict[str, Any]) -> None:
    MODELS_FILE.parent.mkdir(parents=True, exist_ok=True)
    MODELS_FILE.write_text(json.dumps(models, ensure_ascii=False, indent=2) + "\n")


def setup_models_interactive() -> None:
    """Interactive model setup wizard."""
    models = load_models()

    print("AYA Model Setup")
    print("=" * 40)
    print()

    # 1. Check Claude (always available via Agent tool)
    print("[Claude] opus / sonnet / haiku")
    if shutil.which("claude"):
        print("  ✓ claude CLI found — ready via Agent tool")
        for m in ["claude-opus", "claude-sonnet", "claude-haiku"]:
            if m not in models:
                models[m] = {
                    "engine": "claude-agent",
                    "model_id": m.split("-", 1)[1],
                    "status": "ready",
                }
    else:
        print("  ✗ claude CLI not found")
        print("  Fix: npm install -g @anthropic-ai/claude-code")
    print()

    # 2. Check Codex / GPT
    print("[GPT] gpt-5.5 / o3 / o4-mini (via Codex)")
    if shutil.which("codex"):
        print("  ✓ codex CLI found — ready via codex exec")
        if "gpt-5.5" not in models:
            models["gpt-5.5"] = {
                "engine": "codex",
                "model_id": "gpt-5.5",
                "status": "ready",
            }
    else:
        print("  ✗ codex CLI not found")
        print("  Fix: npm install -g @openai/codex")
    print()

    # 3. Third-party models
    print("[Third-party] Deepseek, Qwen, Gemini, etc. (via claude -p)")
    print("  These use claude CLI with --model flag.")
    print("  You need to provide: model name, base URL, API key.")
    print()

    while True:
        ans = input("  Add a third-party model? (y/n): ").strip().lower()
        if ans != "y":
            break

        name = input("  Model name (e.g. deepseek-v4-pro): ").strip()
        if not name:
            continue

        base_url = input("  Base URL (e.g. https://api.deepseek.com/v1): ").strip()
        api_key = input("  API Key: ").strip()

        engine = _detect_engine(name)
        models[name] = {
            "engine": engine,
            "model_id": name,
            "base_url": base_url or None,
            "api_key": api_key or None,
            "status": "configured",
        }
        print(f"  ✓ Added {name} (engine: {engine})")
        print()

    save_models(models)

    ready = [k for k, v in models.items() if v.get("status") in ("ready", "configured")]
    print()
    print(f"Saved to {MODELS_FILE}")
    print(f"{len(ready)} models available: {', '.join(ready)}")


def setup_models_noninteractive(name: str, base_url: str = "", api_key: str = "") -> None:
    """Add a model non-interactively (for scripting / PM use)."""
    models = load_models()
    engine = _detect_engine(name)
    models[name] = {
        "engine": engine,
        "model_id": name,
        "base_url": base_url or None,
        "api_key": api_key or None,
        "status": "configured",
    }
    save_models(models)
    print(f"Added {name} (engine: {engine})")


def list_models() -> None:
    """Print all configured models."""
    models = load_models()
    if not models:
        print("No models configured. Run: python3 -m aya.workspace setup")
        return
    print(f"{'Name':25s} {'Engine':15s} {'Status':12s} Base URL")
    print("-" * 75)
    for name, cfg in models.items():
        url = cfg.get("base_url") or "-"
        print(f"{name:25s} {cfg.get('engine','?'):15s} {cfg.get('status','?'):12s} {url}")


def get_model_env(model_name: str) -> Dict[str, str]:
    """Return environment variables needed to use a model via claude -p.
    PM passes these when spawning a claude-cli worker.
    Uses ANTHROPIC_BASE_URL/ANTHROPIC_AUTH_TOKEN for claude -p compatibility."""
    models = load_models()
    cfg = models.get(model_name, {})
    env = {}
    if cfg.get("base_url"):
        env["ANTHROPIC_BASE_URL"] = cfg["base_url"]
    if cfg.get("api_key"):
        env["ANTHROPIC_AUTH_TOKEN"] = cfg["api_key"]
    return env


def get_model_id_with_context(model_name: str) -> str:
    """Return the model ID with context window suffix if configured.
    e.g., 'deepseek-v4-pro' → 'deepseek-v4-pro[1m]' if models.json has it."""
    models = load_models()
    cfg = models.get(model_name, {})
    model_id = cfg.get("model_id", model_name)
    if model_id == model_name and cfg.get("base_url"):
        return f"{model_name}[1m]"
    return model_id


def route_model(task_type: str) -> Dict[str, str]:
    """Look up the routing table and return the recommended model + engine.
    Falls back to sonnet/claude-agent if task_type is unknown."""
    ws = Workspace(".")
    if ws.exists():
        config = ws.load_config()
        rules = config.get("routing_rules", [])
        for rule in rules:
            if rule.get("task_type") == task_type:
                model_name = rule["prefer"]
                engine = _detect_engine(model_name)
                return {
                    "model": model_name,
                    "engine": engine,
                    "fallback": rule.get("fallback", "claude-sonnet"),
                    "task_type": task_type,
                }
    return {"model": "claude-sonnet", "engine": "claude-agent",
            "fallback": "deepseek-v4-pro", "task_type": task_type}


def generate_spawn_command(
    task_id: str,
    worker_id: str,
    model: str,
    engine: str,
    worktree_path: str,
    runtime_dir: str,
    prompt_file: str,
) -> Dict[str, Any]:
    """Generate the spawn command for a worker based on its engine.

    Returns a dict with 'engine', 'type' ('agent'|'bash'), and 'command'
    (the full command string or Agent tool call args).
    The PM pastes 'command' directly into the appropriate tool call.
    """
    if engine == "claude-agent":
        model_alias = model.replace("claude-", "")
        return {
            "engine": engine,
            "type": "agent",
            "command": {
                "description": f"Worker-{task_id}: see prompt",
                "name": worker_id,
                "model": model_alias,
                "mode": "bypassPermissions",
                "run_in_background": True,
                "prompt": f"<read prompt from {prompt_file}>",
            },
        }
    elif engine == "codex":
        cmd = (
            f"codex exec -m {model} "
            f"--sandbox workspace-write "
            f"--cd {worktree_path} "
            f'--writable-dirs "{runtime_dir}/mailbox {runtime_dir}/board" '
            f"-o {runtime_dir}/logs/{worker_id}/result.txt "
            f'"$(cat {prompt_file})"'
        )
        return {"engine": engine, "type": "bash", "command": cmd}
    else:
        env_vars = get_model_env(model)
        env_prefix = " ".join(f'{k}="{v}"' for k, v in env_vars.items())
        if env_prefix:
            env_prefix += " "
        model_id = get_model_id_with_context(model)
        cmd = (
            f"cd {worktree_path} && {env_prefix}"
            f"claude -p \"$(cat {prompt_file})\" "
            f"--model {model_id} "
            f"--output-format json "
            f"--permission-mode bypassPermissions "
            f"2>/dev/null > {runtime_dir}/logs/{worker_id}/result.json"
        )
        return {"engine": engine, "type": "bash", "command": cmd}


# ---------------------------------------------------------------------------
# Self-update
# ---------------------------------------------------------------------------

REPO_URL = "https://github.com/kuangren777/agent-your-agent.git"
SKILL_DIR = Path.home() / ".claude" / "skills" / "aya"
AYA_SRC_DIR = AYA_HOME / "src"
UPDATE_CHECK_FILE = AYA_HOME / ".last_update_check"
UPDATE_CHECK_INTERVAL = 86400  # 24 hours


def check_for_update() -> Optional[str]:
    """Check if a newer version exists on GitHub. Returns remote version or None.
    Only checks once per 24h (cached in ~/.aya/.last_update_check)."""
    import time

    now = time.time()
    if UPDATE_CHECK_FILE.exists():
        try:
            data = json.loads(UPDATE_CHECK_FILE.read_text())
            if now - data.get("ts", 0) < UPDATE_CHECK_INTERVAL:
                if data.get("has_update"):
                    return data.get("remote_ver")
                return None
        except (json.JSONDecodeError, OSError):
            pass

    from aya import __version__ as local_ver

    r = subprocess.run(
        ["git", "ls-remote", "--tags", REPO_URL],
        capture_output=True, text=True, timeout=5,
    )

    remote_ver = None
    if r.returncode == 0 and r.stdout.strip():
        tags = [l.split("refs/tags/v")[-1] for l in r.stdout.strip().splitlines() if "refs/tags/v" in l]
        if tags:
            remote_ver = sorted(tags)[-1]

    # Fallback: check latest commit's __init__.py via raw URL
    if not remote_ver:
        try:
            r2 = subprocess.run(
                ["git", "ls-remote", REPO_URL, "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            if r2.returncode == 0:
                remote_sha = r2.stdout.strip().split()[0][:7] if r2.stdout.strip() else None
                # Can't get version from sha alone; mark as "check needed"
                remote_ver = None
        except Exception:
            pass

    has_update = remote_ver is not None and remote_ver != local_ver
    UPDATE_CHECK_FILE.parent.mkdir(parents=True, exist_ok=True)
    UPDATE_CHECK_FILE.write_text(json.dumps({
        "ts": now,
        "local_ver": local_ver,
        "remote_ver": remote_ver,
        "has_update": has_update,
    }))

    return remote_ver if has_update else None


def self_update() -> None:
    """Pull latest AYA from GitHub and reinstall to ~/.claude/skills/aya/."""
    import tempfile

    from aya import __version__ as local_ver

    tmp = Path(tempfile.mkdtemp(prefix="aya-update-"))
    try:
        print(f"Current version: {local_ver}")
        print(f"Fetching latest from {REPO_URL} ...")

        r = subprocess.run(
            ["git", "clone", "--depth", "1", REPO_URL, str(tmp)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"Failed to clone: {r.stderr.strip()}")
            return

        # Read remote version
        init_file = tmp / "src" / "aya" / "__init__.py"
        remote_ver = "unknown"
        if init_file.exists():
            for line in init_file.read_text().splitlines():
                if line.startswith("__version__"):
                    remote_ver = line.split('"')[1]

        print(f"Latest version:  {remote_ver}")

        if remote_ver == local_ver:
            print("Already up to date.")
            return

        # Install core to ~/.aya/src/
        AYA_SRC_DIR.mkdir(parents=True, exist_ok=True)
        code_dst = AYA_SRC_DIR / "aya"
        if code_dst.exists():
            shutil.rmtree(code_dst)
        shutil.copytree(str(tmp / "src" / "aya"), str(code_dst))
        pycache = code_dst / "__pycache__"
        if pycache.exists():
            shutil.rmtree(pycache)

        # Install Claude Code skill
        SKILL_DIR.mkdir(parents=True, exist_ok=True)
        skill_src = tmp / ".claude" / "skills" / "aya.md"
        if skill_src.exists():
            shutil.copy2(str(skill_src), str(SKILL_DIR / "SKILL.md"))

        print(f"Updated {local_ver} → {remote_ver}")
        print(f"Core:  {AYA_SRC_DIR}")
        print(f"Skill: {SKILL_DIR}")
        print("Run /reload-plugins in Claude Code to pick up the new version.")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Default config
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
            },
            "claude-sonnet": {
                "engine": "claude-agent",
                "model_id": "sonnet",
                "capabilities": ["implementation", "review", "standard_coding"],
                "swe_bench_verified": 79.6,
                "cost_input_per_mtok": 3.0,
                "cost_output_per_mtok": 15.0,
            },
            "claude-haiku": {
                "engine": "claude-agent",
                "model_id": "haiku",
                "capabilities": ["classification", "simple_edit", "formatting", "routing"],
                "swe_bench_verified": 55.0,
                "cost_input_per_mtok": 1.0,
                "cost_output_per_mtok": 5.0,
            },
            "deepseek-v4-pro": {
                "engine": "claude-cli",
                "model_id": "deepseek-v4-pro",
                "capabilities": ["implementation", "algorithm", "math", "coding", "boilerplate"],
                "swe_bench_verified": 80.6,
                "cost_input_per_mtok": 1.74,
                "cost_output_per_mtok": 3.48,
            },
            "gpt-5.5": {
                "engine": "codex",
                "model_id": "gpt-5.5",
                "capabilities": ["implementation", "testing", "boilerplate", "documentation", "agentic"],
                "swe_bench_verified": 83.0,
                "cost_input_per_mtok": 5.0,
                "cost_output_per_mtok": 30.0,
            },
        },
        "routing_rules": [
            {"task_type": "architecture", "prefer": "claude-opus", "fallback": "claude-sonnet"},
            {"task_type": "complex_refactor", "prefer": "claude-sonnet", "fallback": "deepseek-v4-pro"},
            {"task_type": "implementation", "prefer": "deepseek-v4-pro", "fallback": "claude-sonnet"},
            {"task_type": "testing", "prefer": "gpt-5.5", "fallback": "deepseek-v4-pro"},
            {"task_type": "boilerplate", "prefer": "deepseek-v4-pro", "fallback": "claude-haiku"},
            {"task_type": "review", "prefer": "claude-sonnet", "fallback": "deepseek-v4-pro"},
            {"task_type": "documentation", "prefer": "deepseek-v4-pro", "fallback": "claude-haiku"},
            {"task_type": "debugging", "prefer": "claude-opus", "fallback": "claude-sonnet"},
        ],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli_main() -> None:
    args = sys.argv[1:]
    if not args:
        print("Usage: python3 -m aya.workspace <command> [args]")
        print("Commands: init, list-pms, list-models, write-task, update-task, send-msg,")
        print("          read-inbox, log-event, status, list-tasks,")
        print("          check-file-conflicts, create-worktree, remove-worktree,")
        print("          cleanup-worktrees, check-env, runtime-dir,")
        print("          route-model, spawn-command, spawn-worker,")
        print("          memory-log, memory-stats, memory-patterns, memory-suggest,")
        print("          setup, models, model-env, self-update, version")
        sys.exit(1)

    cmd = args[0]
    ws = Workspace(".")

    if cmd == "list-models":
        models = ws.list_models_full()
        print(f"{'Name':25s} {'Engine':15s} {'Available':10s} Routing Priority")
        print("-" * 80)
        for m in models:
            avail = "yes" if m["available"] else "NO"
            priority = ", ".join(m["routing_priority"]) or "-"
            print(f"{m['name']:25s} {m['engine']:15s} {avail:10s} {priority}")
        return

    elif cmd == "init":
        pm_session = "--pm-session" in args
        name = None
        for i, a in enumerate(args):
            if a == "--name" and i + 1 < len(args):
                name = args[i + 1]
        state = ws.init(name)
        print(f"Initialized AYA for project '{state.project_name}'")
        print(f"  Runtime: {ws.runtime_dir}")
        print(f"  Repo:    {ws.project_dir}")
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

    elif cmd == "create-worktree":
        worker_id = args[1]
        branch = args[2]
        wt_path = ws.create_worktree(worker_id, branch)
        print(f"Worktree: {wt_path}")

    elif cmd == "remove-worktree":
        worker_id = args[1]
        ws.remove_worktree(worker_id)
        print(f"Removed worktree for {worker_id}")

    elif cmd == "cleanup-worktrees":
        count = ws.cleanup_worktrees()
        print(f"Cleaned up {count} worktrees")

    elif cmd == "check-env":
        issues = ws.check_env()
        if issues:
            print("Environment issues:")
            for iss in issues:
                print(f"  [{iss['status']}] {iss['engine']}")
                print(f"    Fix: {iss['fix']}")
        else:
            print("All engines ready.")

    elif cmd == "runtime-dir":
        print(ws.runtime_dir)

    elif cmd == "setup":
        if len(args) > 1:
            # Non-interactive: setup MODEL [--base-url URL] [--api-key KEY]
            model_name = args[1]
            base_url = ""
            api_key = ""
            for i, a in enumerate(args):
                if a == "--base-url" and i + 1 < len(args):
                    base_url = args[i + 1]
                if a == "--api-key" and i + 1 < len(args):
                    api_key = args[i + 1]
            setup_models_noninteractive(model_name, base_url, api_key)
        else:
            setup_models_interactive()
        return

    elif cmd == "models":
        list_models()
        return

    elif cmd == "model-env":
        model_name = args[1]
        env = get_model_env(model_name)
        print(json.dumps(env))
        return

    elif cmd == "route-model":
        task_type = args[1] if len(args) > 1 else ""
        if not task_type:
            print("Usage: route-model TASK_TYPE")
            print("Task types: architecture, complex_refactor, implementation,")
            print("            testing, boilerplate, review, documentation, debugging")
            sys.exit(1)
        # First check memory for adaptive suggestion
        from aya.memory import AyaMemory
        mem = AyaMemory(str(ws.project_dir))
        suggestion = mem.suggest_model(task_type)
        if suggestion.get("source") == "history":
            # Memory has a suggestion based on past performance
            suggestion["note"] = "Based on routing history for this project"
            print(json.dumps(suggestion, indent=2))
        else:
            # Fall back to static routing table
            result = route_model(task_type)
            result["note"] = "Static routing table (no history yet)"
            print(json.dumps(result, indent=2))
        return

    elif cmd == "spawn-command":
        if len(args) < 2:
            print("Usage: spawn-command TASK_ID [--prompt-file PATH]")
            sys.exit(1)
        task_id = args[1]
        prompt_file = ""
        for i, a in enumerate(args):
            if a == "--prompt-file" and i + 1 < len(args):
                prompt_file = args[i + 1]
        task = ws.read_task(task_id)
        wt_path = str(ws.worktree_path(f"worker-{task_id}"))
        rt_dir = str(ws.runtime_dir)
        if not prompt_file:
            prompt_file = f"{rt_dir}/logs/worker-{task_id}/prompt.md"
        result = generate_spawn_command(
            task_id=task_id,
            worker_id=f"worker-{task_id}",
            model=task.model,
            engine=task.engine,
            worktree_path=wt_path,
            runtime_dir=rt_dir,
            prompt_file=prompt_file,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    elif cmd == "spawn-worker":
        if len(args) < 2:
            print("Usage: spawn-worker TASK_ID  (prompt text read from stdin)")
            sys.exit(1)
        task_id = args[1]
        prompt_text = sys.stdin.read()
        result = ws.prepare_spawn(task_id, prompt_text)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    elif cmd == "memory-log":
        if len(args) < 3:
            print("Usage: memory-log TASK_ID JSON")
            sys.exit(1)
        task_id = args[1]
        data = json.loads(args[2])
        from aya.memory import AyaMemory
        mem = AyaMemory(str(ws.project_dir))
        mem.log_routing(
            task_id=task_id,
            task_type=data.get("task_type", ""),
            model=data.get("model", ""),
            engine=data.get("engine", ""),
            success=data.get("success", True),
            cost_usd=data.get("cost_usd", 0),
            turns=data.get("turns", 0),
            duration_ms=data.get("duration_ms", 0),
        )
        print(f"Logged routing for {task_id}: {data.get('model')} {'✓' if data.get('success') else '✗'}")
        return

    elif cmd == "memory-stats":
        from aya.memory import AyaMemory
        mem = AyaMemory(str(ws.project_dir))
        stats = mem.get_model_stats()
        if not stats:
            print("No routing history yet.")
            return
        print(f"{'Model':25s} {'Total':6s} {'Success':8s} {'Rate':6s} {'Avg Cost':9s} {'Avg Turns':10s}")
        print("-" * 70)
        for model, s in sorted(stats.items(), key=lambda x: -x[1]["success_rate"]):
            print(f"{model:25s} {s['total']:6d} {s['successes']:8d} {s['success_rate']:5.0%} ${s['avg_cost']:7.2f} {s['avg_turns']:9.1f}")
        return

    elif cmd == "memory-patterns":
        from aya.memory import AyaMemory
        mem = AyaMemory(str(ws.project_dir))
        patterns = mem.read_patterns()
        if not patterns:
            print("No patterns stored yet.")
            return
        for key, content in sorted(patterns.items()):
            print(f"## {key}")
            print(content)
            print()
        return

    elif cmd == "memory-suggest":
        if len(args) < 2:
            print("Usage: memory-suggest TASK_TYPE")
            sys.exit(1)
        task_type = args[1]
        from aya.memory import AyaMemory
        mem = AyaMemory(str(ws.project_dir))
        suggestion = mem.suggest_model(task_type)
        print(json.dumps(suggestion, indent=2))
        return

    elif cmd == "self-update":
        self_update()
        return

    elif cmd == "version":
        from aya import __version__
        print(f"AYA {__version__}")
        return

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    _cli_main()
