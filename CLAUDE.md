# AYA — Agent Your Agent

Multi-agent orchestration framework built on Claude Code CLI. Uses file-system protocol for inter-agent communication.

## Quick Start

```bash
# Run tests
PYTHONPATH=src python3 -m pytest tests/ -v

# Init workspace
PYTHONPATH=src python3 -m aya.workspace init --pm-session --task "your task"

# Check status
PYTHONPATH=src python3 -m aya.workspace status
```

## Architecture

- `/aya` skill turns current TUI session into PM
- PM spawns Workers via Agent tool (Claude), claude CLI (Deepseek), or codex exec (GPT-5.5)
- All inter-agent communication through `.aya/mailbox/` JSON files
- Shared context via `.aya/board/`
- File ownership (`owned_files`) prevents parallel write conflicts

## Key Files

- `.claude/skills/aya.md` — PM behavior instructions (the skill)
- `src/aya/models.py` — TaskSpec, Message, Event, PMSession dataclasses
- `src/aya/workspace.py` — `.aya/` directory management + CLI tool
