# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

AYA (Agent Your Agent) — a multi-agent orchestration framework that turns a Claude Code session into a PM coordinating parallel workers across multiple LLM backends (Claude, Deepseek, GPT-5.5). Workers communicate via a file-system protocol (JSON files under `~/.aya/runtime/<project-hash>/`), not in-process calls.

## Commands

```bash
# Run all tests
PYTHONPATH=src python3 -m pytest tests/ -v

# Run a single test class or method
PYTHONPATH=src python3 -m pytest tests/test_workspace.py::TestTasks -v
PYTHONPATH=src python3 -m pytest tests/test_models.py::TestTaskSpec::test_roundtrip -v

# Init workspace + register PM session
PYTHONPATH=src python3 -m aya.workspace init --pm-session --task "your task"

# Other CLI commands
PYTHONPATH=src python3 -m aya.workspace status
PYTHONPATH=src python3 -m aya.workspace list-tasks [--pm PM_ID]
PYTHONPATH=src python3 -m aya.workspace check-file-conflicts TASK_ID
PYTHONPATH=src python3 -m aya.workspace check-env
PYTHONPATH=src python3 -m aya.workspace setup          # interactive model config
PYTHONPATH=src python3 -m aya.workspace self-update
```

`PYTHONPATH=src` (or `PYTHONPATH=~/.aya/src` when installed) is always required — there is no `pip install` step.

## Architecture

Two-layer separation:

1. **Runtime/coordination** (`~/.aya/runtime/<project-hash>/`) — tasks, mailbox, board, events, config. Lives outside the repo so worktrees don't duplicate it. A `.aya` symlink in the project root points here for convenience.
2. **Worker worktrees** (`<project>/.aya-worktrees/<worker-id>/`) — each worker gets an isolated git worktree. PM creates/merges/cleans these.

The `/aya` Claude Code skill (`.claude/skills/aya.md`) contains the PM behavior instructions — it's the main entry point. When activated, it turns the current TUI session into a PM that decomposes tasks, routes them to models, spawns workers, and merges results.

### Key modules

- `src/aya/models.py` — All dataclasses: `TaskSpec`, `Message`, `Event`, `PMSession`, `AyaState`, plus factory functions (`create_task`, `create_message`, `create_pm_session`). All use `from_dict`/`to_dict` for JSON serialization, and `from_dict` silently drops unknown keys.
- `src/aya/workspace.py` — `Workspace` class managing the runtime directory, plus the CLI (`_cli_main`). Also contains model setup/routing (`load_models`, `setup_models_interactive`, `get_model_env`, `_detect_engine`) and self-update logic.
- `src/aya/__init__.py` — Only exports `__version__`.

### Worker spawn engines

Workers are spawned via three different mechanisms based on `engine` field:
- `claude-agent` → `Agent()` tool (Claude models: opus/sonnet/haiku)
- `claude-cli` → `claude -p --model <name>` subprocess (third-party models like Deepseek)
- `codex` → `codex exec -m <model>` subprocess (GPT/OpenAI models)

Engine is auto-detected from model name via `_detect_engine()` using prefix matching in `ENGINE_RULES`.

### File conflict safety

`owned_files` on each `TaskSpec` declares exclusive write access. `check_file_conflicts()` compares against all `assigned`/`in_progress` tasks. Two parallel workers must never share an `owned_file`.

## Testing

Tests use `tmp_path` + `monkeypatch` to redirect `AYA_HOME` so nothing touches `~/.aya/`. The `ws` fixture in `test_workspace.py` provides a fully initialized `Workspace`. Worktree tests that need a real git repo are limited to path/list checks since `tmp_path` isn't a git repo.

No external dependencies beyond pytest — the project has zero runtime deps (`dependencies = []` in `pyproject.toml`).
