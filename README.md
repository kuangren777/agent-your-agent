# AYA — Agent Your Agent

> Your agent that manages other agents.

AYA is a lightweight multi-agent orchestration framework built on [Claude Code](https://claude.com/claude-code) CLI. It turns your current Claude Code session into a **Project Manager** that decomposes tasks, routes them to the best model, and coordinates parallel workers — all through a file-system protocol.

## How it works

```
You (Claude Code TUI)
  │  /aya "Build a REST API with auth"
  ▼
PM (your current session)
  ├── Agent(model="opus")     → complex architecture
  ├── claude --model deepseek → standard coding (best price-performance)
  └── codex exec -m gpt-5.5  → test generation
```

**Key design principles:**
- **CLI-native** — orchestration only; Claude Code handles execution
- **File = Protocol** — all agent communication via `.hive/` JSON files
- **Multi-model** — routes tasks to the cheapest capable model
- **Parallel-safe** — `owned_files` prevents concurrent write conflicts

## Quick Start

```bash
# 1. Clone
git clone https://github.com/YOUR_USER/aya.git
cd aya

# 2. Use the /aya skill in any project
cp -r .claude/skills/aya.md /path/to/your/project/.claude/skills/

# 3. In Claude Code TUI:
/aya "Build a calculator with add, subtract, multiply, divide"
```

## Model Routing

AYA picks the best model for each sub-task automatically:

| Task Type | Model | SWE-bench | Cost (output $/M) |
|-----------|-------|-----------|-------------------|
| Architecture / debugging | Claude Opus 4.7 | 87.6% | $25 |
| Standard implementation | Deepseek-v4-pro | 80.6% | **$3.48** |
| Code review | Claude Sonnet 4.6 | 79.6% | $15 |
| Tests / boilerplate | GPT-5.5 | 83% | $30 |

Models are configured in `.hive/config.json` — add new ones anytime.

## File System Protocol

```
.hive/
├── tasks/          # Task specs (PM writes, Workers read)
├── mailbox/        # JSON messages between agents
│   ├── pm-{id}/    # PM inbox
│   └── pm-{id}--worker-{id}/
├── board/          # Shared context (architecture, API specs)
├── config.json     # Model registry + routing rules
├── events.jsonl    # Append-only audit log
└── state.json      # Current project state
```

Workers communicate by reading/writing JSON files — works across Claude Code, Deepseek (via claude CLI), and GPT-5.5 (via codex exec).

## CLI Tools

```bash
export PYTHONPATH=src

# Initialize workspace
python3 -m hive.workspace init --pm-session --task "your task"

# Task management
python3 -m hive.workspace write-task '{"task_id":"task-001",...}'
python3 -m hive.workspace list-tasks
python3 -m hive.workspace update-task task-001 '{"status":"done"}'

# Parallel safety check
python3 -m hive.workspace check-file-conflicts task-001

# Communication
python3 -m hive.workspace send-msg '{"from_agent":"pm","to_agent":"worker-0",...}'
python3 -m hive.workspace read-inbox pm-a3f2

# Status
python3 -m hive.workspace status
```

## Parallel Safety

Each task declares `owned_files` (exclusive write) and `read_files` (shared read). PM checks for conflicts before spawning workers:

```json
{
  "task_id": "task-001",
  "owned_files": ["src/auth.py", "tests/test_auth.py"],
  "read_files": ["src/config.py"]
}
```

Two workers with overlapping `owned_files` will never run in parallel.

## License

MIT
