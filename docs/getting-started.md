# Getting Started with AYA

AYA (Agent Your Agent) is a multi-agent orchestration framework that turns your [Claude Code](https://claude.com/claude-code) session into an autonomous Project Manager. When you invoke `/aya`, your current session becomes a PM that decomposes your request into sub-tasks, routes each to the cheapest capable model (Claude Opus/Sonnet, Deepseek, or GPT-5.5), spawns parallel workers in isolated git worktrees, monitors their progress through a file-system mailbox protocol, merges the results, and delivers a complete solution — all with file-conflict safety guarantees.

---

## Prerequisites

Before installing AYA, make sure you have:

- **Claude Code CLI** — the TUI in which `/aya` runs ([install guide](https://claude.ai/claude-code))
- **Python 3.8+** — used by the `aya.workspace` CLI helper
- **Git** — required for git worktree isolation; the project you work on must be a git repo (AYA will `git init` one if needed)
- **Optional: Codex CLI** — needed only if you want to route tasks to GPT-5.5 via `codex exec`

---

## Installation

### Option A — One-line install (recommended)

```bash
git clone https://github.com/kuangren777/agent-your-agent.git && cd agent-your-agent && ./install.sh
```

### Option B — Pipe from curl

```bash
curl -fsSL https://raw.githubusercontent.com/kuangren777/agent-your-agent/main/install.sh | bash
```

### What the installer does

`install.sh` copies two things into `~/.claude/skills/aya/`:

| What | Where it ends up |
|------|-----------------|
| The skill definition | `~/.claude/skills/aya/SKILL.md` |
| The Python workspace package | `~/.claude/skills/aya/aya/` |

Nothing is added to your PATH or shell profile. The Python package is invoked directly by the PM session via `PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace`.

---

## Verification

After installation, open (or restart) a Claude Code session, then run:

```
/reload-plugins
```

This tells Claude Code to re-scan `~/.claude/skills/`. Afterwards, check that `/aya` appears in the available skills list. You can also verify by running:

```
/aya
```

If installation succeeded you will see AYA enter PM mode and prompt you for a task.

---

## Your First AYA Session

### Step 1 — Give AYA a task

In any Claude Code session, type:

```
/aya "Build a Python hello world package with tests"
```

You can also enter PM mode first and then supply the task:

```
/aya
> Build a Python hello world package with tests
```

Once AYA is active, every subsequent message in the session is treated as a PM instruction — new tasks, follow-up requirements, status queries, or "Exit AYA".

---

### Step 2 — PM initializes the workspace

AYA runs the workspace CLI to create the `.aya/` directory and register a PM session:

```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace init --pm-session --task "Build a Python hello world package with tests"
```

You will see output like:

```
Initialized .aya/ workspace
PM session registered: pm-a3f2
```

AYA also writes your requirements to `.aya/board/requirements.md` so all workers share the same context.

---

### Step 3 — Task decomposition

AYA analyzes the request and breaks it into sub-tasks with explicit file ownership:

```
task-001: Create package structure + hello world module
          owned_files: [src/hello/__init__.py, src/hello/greet.py]
          model: deepseek-v4-pro   (simple implementation, ≤2 files)

task-002: Write tests
          owned_files: [tests/test_greet.py]
          model: deepseek-v4-pro   (boilerplate, cost-optimized)

task-003: Write pyproject.toml + README
          owned_files: [pyproject.toml, README.md]
          model: deepseek-v4-pro   (docs/config, ≤2 files)
```

Each task declares which files it owns (`owned_files`) so AYA can guarantee no two parallel workers touch the same file.

---

### Step 4 — Model routing

AYA picks the cheapest model capable of each task, following these rules:

| Condition | Model assigned |
|-----------|---------------|
| ≤2 files, pure coding | `deepseek-v4-pro` ($3.48/M tokens) |
| 3–5 files, standard implementation | `claude-sonnet` ($15/M) |
| >5 files, architecture, or debugging | `claude-opus` ($25/M) |
| Tests, boilerplate, or documentation | `deepseek-v4-pro` or `gpt-5.5` |

For the hello world example all three tasks route to Deepseek — the lowest-cost option.

---

### Step 5 — Spawn workers in parallel

Because none of the three tasks share `owned_files`, AYA spawns all three simultaneously. For Deepseek workers it runs something like:

```bash
git worktree add .aya/worktrees/worker-task-001 -b agent/task-001
cd .aya/worktrees/worker-task-001 && \
  claude -p '...' --model deepseek-v4-pro --permission-mode bypassPermissions \
  > ../../logs/worker-task-001/result.json
```

Each worker runs in its own git worktree (physical filesystem isolation) and commits with `[aya:task-001]` prefix. Workers write progress and completion messages as JSON files to `.aya/mailbox/pm-a3f2/`.

---

### Step 6 — Monitor and collect results

AYA polls for completion messages:

```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace read-inbox pm-a3f2
```

As each worker finishes you will see something like:

```
[worker-task-001] task-001 done — files: src/hello/__init__.py, src/hello/greet.py — tests: pass
[worker-task-002] task-002 done — files: tests/test_greet.py — tests: pass
[worker-task-003] task-003 done — files: pyproject.toml, README.md
```

---

### Step 7 — Merge and report

AYA merges all worker branches into `dev`, runs integration tests, and reports:

```
Done. 3 tasks, 3 workers, all tests passing.
Total cost: ~$0.04  (3 × Deepseek)

Files created:
  src/hello/__init__.py
  src/hello/greet.py
  tests/test_greet.py
  pyproject.toml
  README.md
```

---

### Giving follow-up instructions

While AYA is in PM mode you can continue directing it naturally:

```
> Add a --name flag to the CLI so users can say "Hello, Alice"
> The greet function should support multiple languages
> What is the current status?
> Show me the cost breakdown
```

### Exiting PM mode

```
> Exit AYA
```

---

## Understanding the `.aya/` Directory

After a session you will find a `.aya/` directory at your project root:

```
.aya/
├── config.json           # Model registry and routing rules (edit to add models)
├── state.json            # Project-level state
├── pms/                  # Registry of PM sessions (supports multi-PM)
├── tasks/                # One JSON file per task
│   ├── task-001.json
│   └── task-002.json
├── mailbox/              # JSON message files between agents
│   ├── pm-a3f2/          # PM's inbox (workers write completion reports here)
│   └── pm-a3f2--worker-task-001/  # Worker's inbox (PM writes directives here)
├── board/                # Shared read-only context for all agents
│   ├── requirements.md   # Written by PM at session start
│   └── architecture.md   # Written by TL for complex projects
├── events.jsonl          # Append-only audit log of every agent action
├── logs/                 # Per-worker stdout/stderr
│   └── worker-task-001/
│       └── result.json
└── worktrees/            # Git worktree per worker (auto-cleaned after merge)
    └── worker-task-001/
```

You can add `.aya/worktrees/` to `.gitignore` (AYA does not do this automatically). The rest of `.aya/` is useful to keep for auditing and multi-session continuity.

### Adding or customizing models

Edit `.aya/config.json` to register new models:

```json
{
  "models": {
    "my-model": {
      "engine": "claude-cli",
      "model_id": "my-model-id",
      "capabilities": ["implementation", "coding"],
      "cost_output_per_mtok": 5.0
    }
  }
}
```

Supported engine values: `claude-agent` (Agent tool), `claude-cli` (Bash `claude -p`), `codex` (Bash `codex exec`).

---

## Multiple AYA Sessions on the Same Project

You can run two AYA sessions on the same project without conflicts. Each session gets its own PM ID, task namespace, and mailbox prefix:

```
Session 1: /aya "Build feature A"  →  PM pm-a3f2, worktrees under pm-a3f2--*
Session 2: /aya "Build feature B"  →  PM pm-b7e1, worktrees under pm-b7e1--*
```

Shared context (architecture docs, API specs) lives in `.aya/board/` and is visible to all sessions.

---

## CLI Reference (quick)

These are the workspace commands the PM calls internally — useful for debugging:

```bash
# View current project status
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace status

# List all PM sessions
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace list-pms

# List tasks
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace list-tasks

# Read PM inbox
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace read-inbox <pm-id>

# Check for file conflicts before spawning a task
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace check-file-conflicts <task-id>

# Update task status manually
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace update-task <task-id> '{"status":"done"}'
```

---

## Next Steps

- **[architecture.md](architecture.md)** — deep dive into the file-system protocol, PM state machine, and worktree isolation model
- **[model-routing.md](model-routing.md)** — full routing rule reference, SWE-bench scores, cost table, and how to tune routing for your project
- **[api-reference.md](api-reference.md)** — complete `aya.workspace` CLI reference, TaskSpec schema, Message format, and event log schema
