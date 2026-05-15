"""
AYA UserPromptSubmit hook — injects PM mode reminder into every user message.

Claude Code calls this hook on every user prompt submission. It reads stdin
for hook input JSON, checks if an AYA PM session is active for the current
project, and returns additionalContext to keep the model in PM mode.

Usage in .claude/settings.json:
  "hooks": {
    "UserPromptSubmit": [{
      "hooks": [{
        "type": "command",
        "command": "PYTHONPATH=~/.aya/src python3 -m aya.hooks"
      }]
    }]
  }

Exit codes:
  0 — success (additionalContext injected or no-op)
  Non-zero — error (Claude Code shows stderr to user)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

AYA_HOME = Path.home() / ".aya"
RUNTIME_BASE = AYA_HOME / "runtime"


def _find_active_pm(project_dir: str) -> dict | None:
    """Find an active PM session for the given project directory."""
    import hashlib

    proj_hash = hashlib.sha256(project_dir.encode()).hexdigest()[:12]
    runtime_dir = RUNTIME_BASE / proj_hash

    state_file = runtime_dir / "state.json"
    if not state_file.exists():
        return None

    try:
        state = json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    if state.get("status") != "running":
        return None

    pm_ids = state.get("pm_sessions", [])
    if not pm_ids:
        return None

    for pm_id in reversed(pm_ids):
        pm_file = runtime_dir / "pms" / f"{pm_id}.json"
        if pm_file.exists():
            try:
                pm = json.loads(pm_file.read_text())
                if pm.get("status") == "running":
                    return {
                        "pm_id": pm_id,
                        "task": pm.get("task", ""),
                        "runtime_dir": str(runtime_dir),
                    }
            except (json.JSONDecodeError, OSError):
                continue

    return None


def _build_sparse_reminder(pm_info: dict) -> str:
    """Build a concise PM mode reminder (sparse version, ~50 tokens)."""
    pm_id = pm_info["pm_id"]
    runtime_dir = pm_info["runtime_dir"]

    return (
        f"AYA PM mode active (session {pm_id}, runtime {runtime_dir}). "
        f"You are the Project Manager. ALL tasks go through AYA's multi-agent pipeline: "
        f"Plan (Explore→Design→Approve) → Decompose → Spawn Workers → Monitor → Merge. "
        f"Use Agent tool to spawn Explore/Plan/Worker agents. "
        f"Use PYTHONPATH=~/.aya/src python3 -m aya.workspace for task/mailbox/status commands. "
        f"See /aya skill instructions (earlier in conversation) for the full workflow. "
        f"Do NOT do implementation work yourself — delegate to workers."
    )


def _build_full_reminder(pm_info: dict) -> str:
    """Build a comprehensive PM mode reminder (full version, ~200 tokens)."""
    pm_id = pm_info["pm_id"]
    task = pm_info["task"]
    runtime_dir = pm_info["runtime_dir"]

    return (
        f"AYA PM mode active — session {pm_id}, runtime {runtime_dir}.\n"
        f"Original task: {task}\n\n"
        f"You are the AYA Project Manager. ALL user requests go through AYA's multi-agent pipeline.\n\n"
        f"## Workflow (see /aya skill for full details)\n"
        f"1. Plan: Explore codebase (Agent subagent_type=Explore) → Design approach (Agent subagent_type=Plan) → Write plan to board/plan.md → Get user approval via AskUserQuestion\n"
        f"2. Decompose: Write TaskSpecs via `PYTHONPATH=~/.aya/src python3 -m aya.workspace write-task '...'`\n"
        f"3. Spawn: Create worktrees, launch workers via Agent tool (run_in_background=true) or claude -p / codex exec\n"
        f"4. Monitor: Read mailbox via `python3 -m aya.workspace read-inbox {pm_id}`\n"
        f"5. Merge: git merge worker branches, run verification, cleanup worktrees\n\n"
        f"## Critical rules\n"
        f"- Do NOT implement code yourself — spawn workers\n"
        f"- Use Agent tool proactively for exploration and planning\n"
        f"- Check file conflicts before spawning: `python3 -m aya.workspace check-file-conflicts TASK_ID`\n"
        f"- Route to cheapest capable model (deepseek $3.48 > haiku $5 > sonnet $15 > opus $25)\n"
        f"- Status: `python3 -m aya.workspace status`"
    )


def _get_prompt_counter_path(runtime_dir: str) -> Path:
    return Path(runtime_dir) / ".hook_prompt_count"


def _read_prompt_count(runtime_dir: str) -> int:
    """Read how many user prompts the hook has seen (all calls, not just emits)."""
    counter_file = _get_prompt_counter_path(runtime_dir)
    if not counter_file.exists():
        return 0
    try:
        return int(counter_file.read_text().strip())
    except (ValueError, OSError):
        return 0


def _increment_prompt_count(runtime_dir: str) -> int:
    """Increment and return the new prompt count. Called on every hook invocation."""
    counter_file = _get_prompt_counter_path(runtime_dir)
    count = _read_prompt_count(runtime_dir) + 1
    try:
        counter_file.write_text(str(count))
    except OSError:
        pass
    return count


TURNS_BETWEEN_REMINDERS = 3
FULL_REMINDER_EVERY_N = 5


def main() -> None:
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        hook_input = {}

    cwd = hook_input.get("cwd", "")
    if not cwd:
        return

    pm_info = _find_active_pm(cwd)
    if pm_info is None:
        return

    runtime_dir = pm_info["runtime_dir"]
    prompt_count = _increment_prompt_count(runtime_dir)

    # First prompt always emits; after that, emit every TURNS_BETWEEN_REMINDERS
    if prompt_count > 1 and (prompt_count - 1) % TURNS_BETWEEN_REMINDERS != 0:
        return

    # How many reminders have we emitted so far (including this one)?
    emit_index = 1 + (prompt_count - 1) // TURNS_BETWEEN_REMINDERS
    if emit_index == 1 or emit_index % FULL_REMINDER_EVERY_N == 0:
        reminder = _build_full_reminder(pm_info)
    else:
        reminder = _build_sparse_reminder(pm_info)

    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": reminder,
        }
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
