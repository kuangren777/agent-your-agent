# AYA — Agent Your Agent

> Your agent that manages other agents.

AYA turns your [Claude Code](https://claude.com/claude-code) session into a **Project Manager** that decomposes tasks, picks the best model for each sub-task, and coordinates parallel workers — all through a file-system protocol.

```
You: /aya "Build a REST API with user auth, item CRUD, and tests"

AYA (PM):
  1. Decomposes into 4 tasks
  2. Routes: auth → Claude Opus, CRUD → Deepseek, tests → GPT-5.5
  3. Spawns 3 workers in parallel (file-conflict safe)
  4. Collects results via .aya/mailbox/
  5. Merges branches, runs integration tests
  6. Reports: "Done. 4 tasks, 3 workers, $0.42 total cost."
```

---

## Install

**One-line install** (copies skill + code to `~/.claude/skills/aya/`):

```bash
git clone https://github.com/kuangren777/agent-your-agent.git && cd agent-your-agent && ./install.sh
```

**Verify** — restart Claude Code (or `/reload-plugins`), then:
```
/aya
```
You should see AYA enter PM mode and wait for your task.

---

## Usage

### Start AYA

```
/aya "Build a Python calculator with add, subtract, multiply, divide — each in its own module, with tests"
```

Or enter PM mode first, then give tasks:
```
/aya
> Build a calculator with four operations
```

Once activated, **every message you send goes through AYA's multi-agent pipeline** until you say "exit AYA".

### What happens next

AYA automatically:

1. **Initializes** `.aya/` workspace in your project directory
2. **Analyzes** your requirements, writes them to `.aya/board/requirements.md`
3. **Decomposes** into sub-tasks with file ownership declarations:
   ```
   task-001: Implement add/subtract    → owned_files: [src/basic.py]
   task-002: Implement multiply/divide → owned_files: [src/advanced.py]
   task-003: Write tests               → owned_files: [tests/]
   ```
4. **Routes** each task to the best model (see [Model Routing](#model-routing))
5. **Spawns workers** in parallel — tasks with no file conflicts run simultaneously
6. **Monitors** progress via `.aya/mailbox/` messages
7. **Merges** all worker branches and runs integration tests
8. **Reports** final results and total cost

### Give follow-up instructions

While AYA is running, you can:
```
> Add input validation to all endpoints
> The auth module needs JWT, not session-based
> What's the current status?
> Show me the cost breakdown
```

### Exit AYA

```
> Exit AYA
```

---

## Model Routing

AYA picks the cheapest model that can handle each task:

| Task Type | Model | SWE-bench | Cost ($/M output) | Engine |
|-----------|-------|-----------|-------------------|--------|
| Architecture / debugging | Claude Opus 4.7 | 87.6% | $25 | `Agent(model="opus")` |
| Complex refactoring (>5 files) | Claude Opus 4.7 | 87.6% | $25 | `Agent(model="opus")` |
| Standard implementation | Deepseek-v4-pro | 80.6% | **$3.48** | `claude --model deepseek-v4-pro` |
| Code review | Claude Sonnet 4.6 | 79.6% | $15 | `Agent(model="sonnet")` |
| Tests / boilerplate | GPT-5.5 | 83% | $30 | `codex exec -m gpt-5.5` |
| Simple edits | Deepseek-v4-pro | 80.6% | **$3.48** | `claude --model deepseek-v4-pro` |

**Cost priority**: Deepseek ($3.48) > Haiku ($5) > Sonnet ($15) > Opus ($25) > GPT-5.5 ($30)

Models are configured in `.aya/config.json` — add new ones anytime:
```json
{
  "models": {
    "your-new-model": {
      "engine": "claude-cli",
      "model_id": "your-model-name",
      "capabilities": ["implementation", "coding"],
      "cost_output_per_mtok": 5.0
    }
  }
}
```

---

## Parallel Safety

Each task declares which files it will modify (`owned_files`) and which it only reads (`read_files`). AYA enforces:

- **No two parallel workers share an `owned_file`** — conflicts are detected before spawning
- **Workers run in isolated git worktrees** — physical filesystem separation
- **`board/` is read-only for workers** — only PM/TL writes shared context

```
task-001: owned_files: [src/auth.py]     ← can run in parallel
task-002: owned_files: [src/api.py]      ← can run in parallel
task-003: owned_files: [src/auth.py]     ← BLOCKED until task-001 completes
```

---

## File System Protocol

All agent communication uses JSON files under `.aya/`:

```
.aya/
├── config.json           # Model registry + routing rules
├── state.json            # Project state
├── pms/                  # PM session registry
├── tasks/                # Task specs (one JSON per task)
│   ├── task-001.json
│   └── task-002.json
├── mailbox/              # Message passing between agents
│   ├── pm-a3f2/          # PM's inbox (workers write here)
│   └── pm-a3f2--worker-0/  # Worker's inbox (PM writes here)
├── board/                # Shared context (architecture, API specs)
│   ├── requirements.md
│   └── architecture.md
├── events.jsonl          # Append-only audit log
└── worktrees/            # Git worktree per worker
```

### Message format

Workers communicate by writing JSON files to mailboxes:
```json
{
  "id": "msg-a1b2c3d4",
  "ts": "2026-05-14T10:30:00Z",
  "from_agent": "worker-0",
  "to_agent": "pm-a3f2",
  "msg_type": "completion",
  "subject": "task-001 done",
  "data": {
    "task_id": "task-001",
    "status": "done",
    "branch": "agent/task-001",
    "files_changed": ["src/auth.py", "tests/test_auth.py"],
    "test_result": "pass"
  }
}
```

---

## Multi-PM Sessions

Multiple AYA sessions can run on the same project without conflicts:

```
Session 1: /aya "Build feature A"  → PM pm-a3f2, workers in mailbox/pm-a3f2--*
Session 2: /aya "Build feature B"  → PM pm-b7e1, workers in mailbox/pm-b7e1--*
```

Each PM has its own mailbox namespace, task set, and worker pool. Shared context lives in `board/`.

A global registry at `~/.aya-registry.json` tracks all projects and their active PM sessions.

---

## CLI Tools

AYA includes Python utilities that the PM calls via Bash:

```bash
# Initialize workspace + register PM session
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace init --pm-session --task "your task"

# Task management
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace write-task '{"task_id":"task-001",...}'
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace list-tasks
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace update-task task-001 '{"status":"done"}'

# Check for file conflicts before parallel spawn
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace check-file-conflicts task-001

# Read agent messages
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace read-inbox pm-a3f2

# View status
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace status
```

---

## How it works under the hood

```
┌─────────────────────────────────────────────────┐
│  Your Claude Code TUI session (= PM)            │
│                                                 │
│  /aya "Build X"                                 │
│    ├── init .aya/, register PM session          │
│    ├── decompose task, write TaskSpecs           │
│    ├── route each task to best model             │
│    │                                             │
│    ├── Agent(model="opus", worktree, background) │
│    │     └── Worker 0: complex auth module       │
│    │                                             │
│    ├── Bash(background): claude -p --model       │
│    │     deepseek-v4-pro                         │
│    │     └── Worker 1: CRUD endpoints            │
│    │                                             │
│    ├── Bash(background): codex exec -m gpt-5.5  │
│    │     └── Worker 2: test generation           │
│    │                                             │
│    ├── read .aya/mailbox/pm/ for results        │
│    ├── merge branches to dev                     │
│    └── report to user                            │
└─────────────────────────────────────────────────┘
```

---

## Development

```bash
# Run tests
PYTHONPATH=src python3 -m pytest tests/ -v

# Test workspace CLI
PYTHONPATH=src python3 -m aya.workspace init --pm-session --task "test"
PYTHONPATH=src python3 -m aya.workspace status
```

## License

MIT
