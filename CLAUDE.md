# AYA — Agent Your Agent

Multi-agent orchestration framework built on Claude Code CLI. Uses file-system protocol for inter-agent communication.

## Quick Start

```bash
# Run tests
PYTHONPATH=src python3 -m pytest tests/ -v

# Init workspace
PYTHONPATH=src python3 -m hive.workspace init --pm-session --task "your task"

# Check status
PYTHONPATH=src python3 -m hive.workspace status
```

## Architecture

- `/aya` skill turns current TUI session into PM
- PM spawns Workers via Agent tool (Claude), claude CLI (Deepseek), or codex exec (GPT-5.5)
- All inter-agent communication through `.hive/mailbox/` JSON files
- Shared context via `.hive/board/`
- File ownership (`owned_files`) prevents parallel write conflicts

## Key Files

- `.claude/skills/aya.md` — PM behavior instructions (the skill)
- `src/hive/models.py` — TaskSpec, Message, Event, PMSession dataclasses
- `src/hive/workspace.py` — `.hive/` directory management + CLI tool
