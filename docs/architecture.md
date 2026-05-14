# AYA Architecture

AYA (Agent Your Agent) is a Claude Code skill that turns the current TUI session into a Project Manager (PM) capable of decomposing tasks, routing sub-tasks to the best-fit model, and orchestrating parallel workers — all without any server, queue, or API beyond the local filesystem.

---

## 1. Overview

Invoking `/aya` in a Claude Code session permanently promotes that session to PM mode. The PM analyzes the user's request, breaks it into discrete sub-tasks, picks the cheapest model capable of each, spawns workers in parallel (where file ownership permits), monitors their progress via mailbox messages, merges results, and reports total cost.

```
User: /aya "Build a REST API with auth, CRUD, and tests"

PM:
  1. Init .aya/ workspace, register PM session (pm-a3f2)
  2. Write requirements to .aya/board/requirements.md
  3. Decompose into TaskSpecs (task-001 auth, task-002 CRUD, task-003 tests)
  4. Route: auth → claude-opus, CRUD → deepseek-v4-pro, tests → gpt-5.5
  5. Spawn 3 workers in parallel (no owned_file overlap)
  6. Poll .aya/mailbox/pm-a3f2/ for completion messages
  7. Merge branches to dev, run integration tests
  8. Report: "Done. 3 workers, $0.42 total cost."
```

---

## 2. Design Principles

### CLI-Native

AYA reuses the tools Claude Code already has: the `Agent` tool for sub-agents, `Bash` for background shell processes, `Read`/`Write` for file I/O. No custom daemon, no socket, no REST API.

### File = Protocol

Every piece of inter-agent communication is a JSON file under `.aya/`. There is no in-process message bus. A worker writes a completion report by calling the `Write` tool. The PM reads it by listing directory contents and parsing JSON. Any agent from any execution engine can participate as long as it can read and write files.

### Parallel Safety via File Ownership

Each `TaskSpec` declares `owned_files` (exclusive write access) and `read_files` (shared read access). Before spawning any worker, the PM calls `check-file-conflicts` to verify that no currently-running task claims any of the same `owned_files`. Workers also run in isolated git worktrees, giving physical filesystem separation in addition to the logical ownership check.

### Multi-Model Routing

The PM selects models based on task complexity and a cost-first heuristic:

| Task Type | Preferred Model | Engine | SWE-bench | $/M output |
|-----------|----------------|--------|-----------|------------|
| Architecture / debugging | claude-opus | claude-agent | 87.6% | $25 |
| Complex refactor (>5 files) | claude-opus | claude-agent | 87.6% | $25 |
| Standard implementation | deepseek-v4-pro | claude-cli | 80.6% | $3.48 |
| Code review | claude-sonnet | claude-agent | 79.6% | $15 |
| Tests / boilerplate | deepseek-v4-pro | claude-cli | 80.6% | $3.48 |
| Documentation | gpt-5.5 | codex | 83% | $30 |
| Simple edits | deepseek-v4-pro | claude-cli | 80.6% | $3.48 |

Cost priority: Deepseek ($3.48) > Haiku ($5) > Sonnet ($15) > Opus ($25) > GPT-5.5 ($30)

Routing rules are stored in `.aya/config.json` and can be extended or overridden at any time.

---

## 3. Role Hierarchy

```
┌──────────────────────────────────────────────────────┐
│  Claude Code TUI Session                             │
│  Role: PM (Project Manager)                          │
│  Identity: pm-{4-hex-id}  e.g. pm-a3f2              │
│                                                      │
│  Responsibilities:                                   │
│  - Receive task from user                            │
│  - Init .aya/ workspace                              │
│  - Write requirements to board/                      │
│  - Optionally spawn TL for architecture planning     │
│  - Decompose, route, and spawn workers               │
│  - Monitor mailbox, handle questions/failures        │
│  - Merge branches, run integration tests             │
│  - Report to user                                    │
└──────────────────────┬───────────────────────────────┘
                       │ (optional, for complex projects)
                       ▼
┌──────────────────────────────────────────────────────┐
│  Plan Sub-agent (Team Leader)                        │
│  Model: claude-opus                                  │
│  Type: one-shot sub-agent (no team_name)             │
│                                                      │
│  Responsibilities:                                   │
│  - Read board/requirements.md                        │
│  - Write board/architecture.md                       │
│  - Write board/api-spec.json                         │
│  - Return refined TaskSpec list with file ownership  │
└──────────────────────┬───────────────────────────────┘
                       │ spawns
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
   ┌───────────┐ ┌───────────┐ ┌───────────┐
   │ Worker-0  │ │ Worker-1  │ │ Worker-2  │
   │ opus/     │ │ deepseek  │ │ gpt-5.5   │
   │ sonnet    │ │ via CLI   │ │ via codex │
   └───────────┘ └───────────┘ └───────────┘
```

**PM** — the current Claude Code TUI session. Persistent throughout the user's session. Has full project file access, can read all mailboxes and task states, and is the sole writer of `board/` context.

**TL (Team Leader)** — an optional one-shot `Plan` sub-agent (claude-opus) used for complex projects (3+ modules). Produces architectural documents that all workers then read. Returns its results to the PM and terminates.

**Workers** — fire-and-forget agents. Each handles exactly one `TaskSpec`. They write progress and completion messages to the PM's mailbox and commit their work to a dedicated git branch. Workers may not write to `board/`.

---

## 4. File System Protocol

### Directory Tree

```
.aya/
├── state.json            # Global project state (AyaState)
├── config.json           # Model registry + routing rules + engine configs
├── events.jsonl          # Append-only audit log (one JSON object per line)
│
├── pms/                  # PM session registry
│   ├── pm-a3f2.json      # PMSession record
│   └── pm-b7e1.json
│
├── tasks/                # One TaskSpec JSON file per task
│   ├── task-001.json
│   ├── task-002.json
│   └── task-003.json
│
├── mailbox/              # Message passing; one sub-directory per inbox
│   ├── pm-a3f2/          # PM's inbox: workers write completion/progress here
│   │   ├── 20260514T103000-worker-0-completion.json
│   │   └── 20260514T103200-worker-1-progress.json
│   ├── pm-a3f2--worker-0/  # Worker-0's inbox: PM writes assign/answer here
│   └── pm-a3f2--worker-1/
│
├── board/                # Shared read-only context for workers
│   ├── requirements.md   # Written by PM from user input
│   ├── architecture.md   # Written by TL (or PM directly)
│   └── api-spec.json     # Written by TL
│
├── checkpoints/          # Reserved for future state snapshots
├── worktrees/            # Git worktree root (one per worker)
│   ├── worker-task-001/  # Checked out to branch agent/task-001
│   └── worker-task-002/
└── logs/                 # Execution logs from CLI/codex workers
    ├── worker-task-001/
    │   └── result.json
    └── worker-task-002/
```

A global registry at `~/.aya-registry.json` tracks all projects and their active PM sessions across the filesystem.

### state.json (AyaState)

Tracks project-level metadata. Updated atomically via a `.tmp` rename to prevent corruption.

| Field | Type | Description |
|-------|------|-------------|
| `project_name` | str | Directory name by default |
| `status` | str | "running" \| "done" \| "failed" |
| `pm_sessions` | list[str] | IDs of all registered PM sessions |
| `total_cost_usd` | float | Accumulated cost across all sessions |
| `started_at` | str | ISO-8601 UTC timestamp |
| `version` | str | AYA schema version |

### config.json

Contains three top-level keys: `models` (capability + cost metadata per model), `routing_rules` (ordered list of task_type → preferred model), and `engine_configs` (spawn command templates per engine type).

### tasks/ — TaskSpec

Each file is a self-contained task specification:

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | str | Unique ID, e.g. "task-a1b2c3d4" |
| `title` | str | Short human-readable name |
| `description` | str | Full task description for the worker |
| `status` | str | "pending" \| "assigned" \| "in_progress" \| "done" \| "failed" |
| `pm_session` | str | Parent PM session ID |
| `assigned_to` | str\|null | Worker ID once assigned |
| `branch` | str\|null | Git branch, e.g. "agent/task-a1b2c3d4" |
| `depends_on` | list[str] | Task IDs that must be "done" before this can run |
| `owned_files` | list[str] | Files this worker has exclusive write access to |
| `read_files` | list[str] | Files this worker reads but does not own |
| `acceptance_criteria` | list[str] | Checklist the worker must satisfy |
| `engine` | str | "claude-agent" \| "claude-cli" \| "codex" |
| `model` | str | Model ID as defined in config.json |
| `created_at` | str | ISO-8601 UTC timestamp |
| `updated_at` | str | ISO-8601 UTC timestamp (auto-updated on write) |
| `result` | str\|null | Summary written by worker on completion |

### mailbox/ — Inbox Namespacing

Each agent has a dedicated inbox directory. Naming convention:

- `pm-{id}/` — the PM's inbox; all workers write here
- `pm-{id}--worker-{task_id}/` — a specific worker's inbox; PM writes assign/answer messages here

Message files are named `{timestamp}-{from_agent}-{msg_type}.json` so lexicographic sort equals arrival order.

### board/ — Shared Context

Writable only by PM and TL. Workers treat all files here as read-only reference material. Typical contents:

- `requirements.md` — verbatim + reformatted user requirements
- `architecture.md` — system design, component diagram, key decisions
- `api-spec.json` — interface contracts between components

### events.jsonl — Audit Log

Append-only JSONL file. Each line is an `Event` object:

```json
{"seq": 1, "ts": "2026-05-14T10:00:00+00:00", "actor": "pm", "event_type": "workspace.init", "data": {}}
{"seq": 2, "ts": "2026-05-14T10:00:01+00:00", "actor": "pm", "event_type": "task.created", "data": {"task_id": "task-001"}}
{"seq": 3, "ts": "2026-05-14T10:01:00+00:00", "actor": "pm", "event_type": "agent.spawned", "data": {"worker": "worker-0", "model": "sonnet"}}
```

Writes are protected by `fcntl.LOCK_EX` to allow concurrent appends from multiple agents on the same host.

---

## 5. Message Protocol

### Message JSON Format

```json
{
  "id": "msg-a1b2c3d4",
  "ts": "2026-05-14T10:30:00+00:00",
  "from_agent": "worker-0",
  "to_agent": "pm-a3f2",
  "msg_type": "completion",
  "subject": "task-001 done",
  "body": "Implemented JWT auth with refresh tokens.",
  "data": {
    "task_id": "task-001",
    "status": "done",
    "branch": "agent/task-001",
    "files_changed": ["src/auth.py", "tests/test_auth.py"],
    "test_result": "pass",
    "summary": "JWT auth module with login, logout, refresh endpoints."
  }
}
```

### Message Types

| msg_type | Direction | Meaning |
|----------|-----------|---------|
| `assign` | PM → Worker | Initial task assignment; carries the full TaskSpec |
| `progress` | Worker → PM | Intermediate status update (optional) |
| `completion` | Worker → PM | Task finished; includes branch, files changed, test result |
| `failure` | Worker → PM | Task failed; includes error details and partial state |
| `question` | Worker → PM | Worker is blocked; needs clarification before continuing |
| `answer` | PM → Worker | PM's response to a question |
| `escalation` | Worker → PM | Unrecoverable situation requiring PM intervention |
| `cancel` | PM → Worker | PM instructs worker to stop (e.g. requirements changed) |

---

## 6. Communication Flow

```
User                PM (TUI)           TL (Plan)         Worker-N
 |                     |                   |                  |
 |--/aya "task"------->|                   |                  |
 |                     |--init .aya/------>|                  |
 |                     |--write board/req--|                  |
 |                     |                  |                   |
 |         (complex projects only)        |                   |
 |                     |--Agent(Plan)----->|                   |
 |                     |                  |--write board/arch--|
 |                     |<--refined tasks--|                   |
 |                     |                  |                   |
 |                     |--check-file-conflicts                |
 |                     |--write tasks/task-N.json             |
 |                     |--Agent/Bash/codex exec-------------->|
 |                     |   (run_in_background=true)           |
 |                     |                                      |
 |                     |           [Worker reads board/,      |
 |                     |            does work, commits]       |
 |                     |                                      |
 |                     |<--Write mailbox/pm-a3f2/             |
 |                     |      *-progress.json                 |
 |                     |                                      |
 |                     |<--Write mailbox/pm-a3f2/             |
 |                     |      *-completion.json               |
 |                     |                                      |
 |                     |--update-task status=done             |
 |                     |--git merge agent/task-N → dev        |
 |                     |--run integration tests               |
 |<--final report------|                                      |
```

---

## 7. Parallel Execution

### owned_files Mutual Exclusion

Before spawning any worker, the PM calls:

```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace check-file-conflicts TASK_ID
```

The conflict detection algorithm (`Workspace.check_file_conflicts`):

1. Load the candidate task's `owned_files` as set `O`.
2. Iterate all other tasks whose `status` is "assigned" or "in_progress".
3. For each such task, compute `O ∩ other.owned_files`.
4. If the intersection is non-empty, record a conflict: `"{other_task_id} ({assignee}): {overlapping_files}"`.
5. Return the list of conflicts. Non-empty list → block spawn.

```
Example:

task-001: owned_files: [src/auth.py]     status: in_progress
task-002: owned_files: [src/api.py]      status: in_progress
task-003: owned_files: [src/auth.py]     status: pending

check-file-conflicts task-003:
  → CONFLICTS: task-001 (worker-0): ['src/auth.py']
  → task-003 is blocked until task-001 completes
```

### depends_on Ordering

A task may not be spawned until all task IDs in its `depends_on` list have `status == "done"`. This handles logical ordering (e.g., "implement schema before writing queries") independently of file ownership.

### Git Worktree Isolation

Each worker runs in a dedicated git worktree under `.aya/worktrees/worker-{task_id}/`, checked out to a fresh branch `agent/{task_id}`. This means:

- Workers' file changes are physically isolated — no cross-contamination.
- The PM merges branches sequentially after all workers complete.
- Merge conflicts are resolved by the PM (or a dedicated merge sub-agent).

---

## 8. Multi-PM Sessions

Multiple `/aya` invocations on the same project create independent PM sessions, each with its own namespace:

```
Session 1: /aya "Build feature A"  → PM pm-a3f2
  Tasks:    task-001, task-002
  Mailbox:  .aya/mailbox/pm-a3f2/
            .aya/mailbox/pm-a3f2--worker-task-001/
            .aya/mailbox/pm-a3f2--worker-task-002/

Session 2: /aya "Build feature B"  → PM pm-b7e1
  Tasks:    task-003, task-004
  Mailbox:  .aya/mailbox/pm-b7e1/
            .aya/mailbox/pm-b7e1--worker-task-003/
```

**Namespace isolation** is enforced by the PM-prefixed mailbox directories. Workers from session 1 cannot accidentally write to session 2's inbox, and `list-tasks --pm pm-a3f2` filters by `pm_session` field.

**Shared resources** — `board/` and `events.jsonl` are project-global. Multiple PMs may read from `board/`. Writes to `events.jsonl` are serialized via `fcntl` locking.

**Global registry** at `~/.aya-registry.json` records every project directory → PM session mapping, enabling discovery across projects:

```json
{
  "/home/user/my-project": {
    "pms": {
      "pm-a3f2": {"started": "2026-05-14T10:00:00Z", "task": "Build feature A", "status": "running"},
      "pm-b7e1": {"started": "2026-05-14T11:00:00Z", "task": "Build feature B", "status": "done"}
    }
  }
}
```

---

## 9. Execution Engines

AYA supports three ways to spawn workers, selected per-task via the `engine` field.

### Engine 1: claude-agent (Claude via Agent Tool)

Used for tasks assigned to claude-sonnet or claude-opus.

```
Agent({
  description: "Worker-{id}: {title}",
  model: "sonnet" | "opus",
  isolation: "worktree",
  mode: "bypassPermissions",
  run_in_background: true,
  prompt: "..."
})
```

- Full Claude Code tool access (Read, Write, Edit, Bash, Agent, etc.)
- Runs as a background sub-agent; PM is notified on completion
- Worktree isolation is handled by the Agent tool's `isolation` parameter
- Can call `SendMessage` if spawned as a teammate (persistent mode)

### Engine 2: claude-cli (Deepseek via claude CLI)

Used for cost-sensitive tasks routed to deepseek-v4-pro.

```bash
git worktree add .aya/worktrees/worker-{task_id} -b agent/task-{id}
mkdir -p .aya/mailbox/{pm_id}--worker-{task_id} .aya/logs/worker-{task_id}

cd .aya/worktrees/worker-{task_id} && \
claude -p '{worker_prompt}' \
  --model deepseek-v4-pro \
  --output-format json \
  --permission-mode bypassPermissions \
  > ../../logs/worker-{task_id}/result.json
```

Run via `Bash(run_in_background=true)`. The worker writes messages directly to the mailbox using the `Write` tool in its own Claude Code subprocess. The PM polls `read-inbox` to detect completion.

### Engine 3: codex (GPT-5.5 via codex exec)

Used for test generation, documentation, and boilerplate where GPT-5.5's capabilities are preferred.

```bash
git worktree add .aya/worktrees/worker-{task_id} -b agent/task-{id}
mkdir -p .aya/mailbox/{pm_id}--worker-{task_id} .aya/logs/worker-{task_id}

codex exec -m gpt-5.5 \
  --sandbox workspace-write \
  --cd .aya/worktrees/worker-{task_id} \
  --writable-dirs "$(pwd)/.aya/mailbox/{pm_id} $(pwd)/.aya/board" \
  -o .aya/logs/worker-{task_id}/result.txt \
  '{worker_prompt}'
```

- Runs in a `workspace-write` sandbox; `--writable-dirs` grants access only to the mailbox and board
- The worker cannot escape its worktree except to write messages and read board context
- Output is captured to `logs/worker-{task_id}/result.txt` for PM inspection

### Engine Comparison

| Property | claude-agent | claude-cli | codex |
|----------|-------------|------------|-------|
| Spawn mechanism | Agent tool | Bash subprocess | Bash subprocess |
| Completion notification | Automatic (PM notified) | Poll mailbox | Poll mailbox |
| Filesystem access | Full (Claude Code tools) | Full (Claude Code tools) | Sandboxed (`workspace-write`) |
| Supports mid-task questions | Yes (SendMessage) | Yes (--resume) | Limited |
| Cost sensitivity | Medium–High | Low (Deepseek) | High (GPT-5.5) |

---

## 10. CLI Utility

The `aya.workspace` module doubles as a CLI tool the PM calls via `Bash`:

```bash
# Initialize workspace and register a PM session
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace init --pm-session --task "..."

# Task lifecycle
python3 -m aya.workspace write-task   '{...}'           # Create/overwrite TaskSpec
python3 -m aya.workspace update-task  TASK_ID '{...}'   # Patch specific fields
python3 -m aya.workspace list-tasks   [--pm PM_ID]      # List tasks (optionally filtered)
python3 -m aya.workspace check-file-conflicts TASK_ID   # Pre-spawn conflict check

# Messaging
python3 -m aya.workspace send-msg     '{...}'           # Write message to recipient's inbox
python3 -m aya.workspace read-inbox   AGENT_ID          # Dump all messages in inbox

# Observability
python3 -m aya.workspace log-event    '{...}'           # Append to events.jsonl
python3 -m aya.workspace status                         # Human-readable project summary
python3 -m aya.workspace list-pms                       # List all PM sessions
```

All commands are stateless reads or atomic writes; the CLI can be called concurrently from multiple workers without coordination beyond the file-lock on `events.jsonl` and the atomic rename for `state.json`.
