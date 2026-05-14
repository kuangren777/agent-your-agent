# AYA API Reference

Complete reference for the AYA (Agent Your Agent) multi-agent orchestration framework.

All CLI commands use the prefix:
```
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace
```

---

## CLI Commands

### `init`

Initialize the `.aya/` workspace directory for the current project.

```
init [--pm-session] [--name NAME] [--task TASK]
```

**Arguments**

| Argument | Type | Description |
|---|---|---|
| `--pm-session` | flag | Also register a new PM session after init |
| `--name NAME` | string | Override project name (defaults to directory name) |
| `--task TASK` | string | Task description for the PM session (requires `--pm-session`) |

**Example**

```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace init --pm-session --name my-project --task "Build REST API"
```

```
Initialized .aya/ for project 'my-project'
PM Session: pm-3a7f
```

Creates subdirectories: `tasks/`, `pms/`, `board/`, `checkpoints/`, `worktrees/`, `logs/`, `mailbox/`, and the files `state.json`, `config.json`, `events.jsonl`.

---

### `list-pms`

List all registered PM sessions for the current project.

```
list-pms
```

Outputs one JSON object per line, each a serialized `PMSession`.

**Example**

```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace list-pms
```

```json
{"id": "pm-3a7f", "task": "Build REST API", "status": "running", "workers": [], "started_at": "2026-05-14T10:00:00+00:00", "total_cost_usd": 0.0}
```

---

### `write-task JSON`

Write (create or overwrite) a task to `.aya/tasks/<task_id>.json`. Automatically sets `updated_at` to now.

```
write-task JSON
```

**Arguments**

| Argument | Description |
|---|---|
| `JSON` | Serialized `TaskSpec` object as a JSON string |

**Example**

```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace write-task \
  '{"task_id":"task-abc123","title":"Add auth","description":"Implement JWT auth","status":"pending","engine":"claude-agent","model":"sonnet"}'
```

```
Wrote task task-abc123
```

---

### `update-task TASK_ID JSON`

Patch an existing task with partial fields. Only known `TaskSpec` fields are applied. `updated_at` is refreshed automatically.

```
update-task TASK_ID JSON
```

**Arguments**

| Argument | Description |
|---|---|
| `TASK_ID` | Task identifier, e.g. `task-abc123` |
| `JSON` | Partial JSON object with fields to update |

**Example**

```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace update-task task-abc123 '{"status":"done","result":"Auth implemented with RS256 tokens"}'
```

```
Updated task-abc123: {'status': 'done', 'result': 'Auth implemented with RS256 tokens'}
```

---

### `list-tasks [--pm PM_ID]`

List all tasks, optionally filtered by PM session ID. Outputs one JSON object per line.

```
list-tasks [--pm PM_ID]
```

**Arguments**

| Argument | Description |
|---|---|
| `--pm PM_ID` | Filter tasks belonging to this PM session |

**Example**

```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace list-tasks --pm pm-3a7f
```

```json
{"task_id": "task-abc123", "title": "Add auth", "status": "done", ...}
```

---

### `check-file-conflicts TASK_ID`

Check whether any `owned_files` in the given task conflict with files already owned by other tasks that are `assigned` or `in_progress`.

```
check-file-conflicts TASK_ID
```

**Arguments**

| Argument | Description |
|---|---|
| `TASK_ID` | Task to check for file ownership conflicts |

**Example**

```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace check-file-conflicts task-abc123
```

No conflicts:
```
No file conflicts.
```

With conflicts:
```
CONFLICTS:
  task-xyz789 (worker-002): ['src/auth.py', 'src/middleware.py']
```

---

### `send-msg JSON`

Place a message in the recipient agent's mailbox (`.aya/mailbox/<to_agent>/`).

```
send-msg JSON
```

**Arguments**

| Argument | Description |
|---|---|
| `JSON` | Serialized `Message` object as a JSON string |

**Example**

```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace send-msg \
  '{"id":"msg-11aa22bb","ts":"2026-05-14T10:05:00+00:00","from_agent":"pm-3a7f","to_agent":"worker-001","msg_type":"task.assigned","subject":"New task","body":"Please implement JWT auth","data":{"task_id":"task-abc123"}}'
```

```
Sent task.assigned from pm-3a7f to worker-001
```

---

### `read-inbox AGENT_ID`

Read all messages in an agent's mailbox, sorted by filename (timestamp order). Outputs one JSON object per line.

```
read-inbox AGENT_ID
```

**Arguments**

| Argument | Description |
|---|---|
| `AGENT_ID` | Agent whose inbox to read, e.g. `worker-001` or `pm-3a7f` |

**Example**

```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace read-inbox worker-001
```

```json
{"id": "msg-11aa22bb", "ts": "2026-05-14T10:05:00+00:00", "from_agent": "pm-3a7f", "to_agent": "worker-001", "msg_type": "task.assigned", ...}
```

---

### `log-event JSON`

Append an event to `.aya/events.jsonl`. Auto-assigns the next sequence number.

```
log-event JSON
```

**Arguments**

| Argument | Description |
|---|---|
| `JSON` | Object with `actor`, `event_type`, and optional `data` fields |

**Example**

```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace log-event \
  '{"actor":"pm-3a7f","event_type":"task.created","data":{"task_id":"task-abc123"}}'
```

```
Event #3: task.created
```

---

### `status`

Print a human-readable status table for the current project, showing all PM sessions and tasks.

```
status
```

**Example**

```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace status
```

```
Project: my-project  Status: running
Total cost: $0.12

PM Sessions:
  pm-3a7f  running     Build REST API

Tasks:
  ID           Status       Model              Assigned   Title
  ---------------------------------------------------------------------------
  task-abc123  done         sonnet             worker-001 Add auth
  task-def456  in_progress  deepseek-v4-pro    worker-002 Write unit tests
```

---

## Data Models

### `TaskSpec`

Represents a unit of work assigned to a worker agent.

| Field | Type | Default | Description |
|---|---|---|---|
| `task_id` | `str` | required | Unique identifier, e.g. `task-abc123` |
| `title` | `str` | required | Short human-readable title |
| `description` | `str` | required | Full task description passed to the worker |
| `status` | `str` | `"pending"` | Lifecycle status: `pending`, `assigned`, `in_progress`, `done`, `failed`, `blocked` |
| `pm_session` | `str` | `""` | ID of the PM session that owns this task |
| `assigned_to` | `Optional[str]` | `None` | Worker agent ID |
| `branch` | `Optional[str]` | `None` | Git branch for this task, auto-set to `agent/<task_id>` by `create_task()` |
| `depends_on` | `List[str]` | `[]` | Task IDs that must complete before this task can start |
| `owned_files` | `List[str]` | `[]` | Files this task has exclusive write access to; used for conflict detection |
| `read_files` | `List[str]` | `[]` | Files this task reads but does not own |
| `acceptance_criteria` | `List[str]` | `[]` | Conditions the worker must satisfy for the task to be considered done |
| `engine` | `str` | `"claude-agent"` | Execution engine: `claude-agent`, `claude-cli`, or `codex` |
| `model` | `str` | `"sonnet"` | Model shortname: `opus`, `sonnet`, `haiku`, `deepseek-v4-pro`, `gpt-5.5` |
| `created_at` | `str` | `""` | ISO 8601 UTC timestamp |
| `updated_at` | `str` | `""` | ISO 8601 UTC timestamp, refreshed on every write |
| `result` | `Optional[str]` | `None` | Free-text summary of what the worker produced |

---

### `Message`

A message delivered to an agent's mailbox.

| Field | Type | Default | Description |
|---|---|---|---|
| `id` | `str` | required | Unique identifier, e.g. `msg-11aa22bb` |
| `ts` | `str` | required | ISO 8601 UTC timestamp |
| `from_agent` | `str` | required | Sender agent ID |
| `to_agent` | `str` | required | Recipient agent ID |
| `msg_type` | `str` | required | Message type (see below) |
| `subject` | `str` | required | One-line subject |
| `body` | `str` | `""` | Free-text message body |
| `data` | `Dict[str, Any]` | `{}` | Structured payload (task IDs, results, etc.) |

**`msg_type` values**

| Value | Meaning |
|---|---|
| `task.assigned` | PM assigns a task to a worker |
| `task.done` | Worker reports successful task completion |
| `task.failed` | Worker reports task failure with error details |
| `task.blocked` | Worker is blocked and needs PM intervention |
| `task.update` | Worker sends a progress update |
| `pm.directive` | PM sends instructions or a correction to a worker |
| `worker.ready` | Worker signals it is idle and ready for work |
| `handoff` | One worker hands an artifact to another worker |

**`filename` property**

Messages are stored as `<ts_compact>-<from_agent>-<msg_type>.json` to ensure mailbox ordering by arrival time.

---

### `Event`

An append-only audit log entry written to `.aya/events.jsonl` (newline-delimited JSON).

| Field | Type | Default | Description |
|---|---|---|---|
| `seq` | `int` | required | Monotonically increasing sequence number |
| `ts` | `str` | required | ISO 8601 UTC timestamp |
| `actor` | `str` | required | Agent ID that generated the event |
| `event_type` | `str` | required | Event type (see below) |
| `data` | `Dict[str, Any]` | `{}` | Structured event payload |

**Common `event_type` values**

| Value | Meaning |
|---|---|
| `pm.started` | A PM session has been registered |
| `task.created` | A new task was written |
| `task.assigned` | A task was assigned to a worker |
| `task.status_changed` | Task status transitioned |
| `task.done` | Task completed successfully |
| `task.failed` | Task failed |
| `worker.spawned` | A worker agent was spawned |
| `worker.idle` | A worker finished and went idle |
| `conflict.detected` | A file ownership conflict was found |
| `pm.completed` | All tasks done; PM session finished |

---

### `PMSession`

Tracks a single PM orchestration session.

| Field | Type | Default | Description |
|---|---|---|---|
| `id` | `str` | required | Unique identifier, e.g. `pm-3a7f` |
| `task` | `str` | required | High-level task description for this session |
| `status` | `str` | `"running"` | Session status: `running`, `done`, `failed` |
| `workers` | `List[str]` | `[]` | Worker agent IDs spawned in this session |
| `started_at` | `str` | `""` | ISO 8601 UTC timestamp |
| `total_cost_usd` | `float` | `0.0` | Accumulated cost for all workers in this session |

---

### `AyaState`

Top-level project state stored in `.aya/state.json`.

| Field | Type | Default | Description |
|---|---|---|---|
| `project_name` | `str` | required | Project name (defaults to directory name) |
| `status` | `str` | `"running"` | Project status: `running`, `done`, `paused` |
| `pm_sessions` | `List[str]` | `[]` | IDs of all PM sessions registered for this project |
| `total_cost_usd` | `float` | `0.0` | Total accumulated cost across all sessions |
| `started_at` | `str` | `""` | ISO 8601 UTC timestamp of first init |
| `version` | `str` | `"0.1.0"` | AYA framework version |

---

## Workspace Class

`Workspace(project_dir=".")` — manages the `.aya/` directory for a project.

### Initialization

```python
ws = Workspace(project_dir=".")
```

| Attribute | Description |
|---|---|
| `project_dir` | Resolved absolute path to the project root |
| `aya_dir` | `project_dir / ".aya"` |

---

### Init & State

#### `init(project_name=None) -> AyaState`

Create `.aya/` subdirectory structure and write `state.json` and `config.json`. Idempotent — returns existing state if already initialized.

#### `load_state() -> AyaState`

Read and return the current `AyaState` from `.aya/state.json`.

#### `save_state(state: AyaState) -> None`

Atomically write `AyaState` to `.aya/state.json` (write to `.tmp` then `os.replace`).

#### `load_config() -> Dict[str, Any]`

Read `.aya/config.json` and return the raw dict. Contains model registry and routing rules.

#### `exists() -> bool`

Return `True` if `.aya/` directory exists.

---

### PM Session

#### `register_pm(task: str) -> PMSession`

Create a new `PMSession` for the given task description. Writes to `.aya/pms/<pm_id>.json`, creates `.aya/mailbox/<pm_id>/`, appends the ID to `AyaState.pm_sessions`, and registers the session in `~/.aya-registry.json`.

#### `list_pms() -> List[PMSession]`

Return all PM sessions sorted by filename (creation order).

---

### Tasks

#### `write_task(task: TaskSpec) -> None`

Write a `TaskSpec` to `.aya/tasks/<task_id>.json`. Updates `task.updated_at`.

#### `read_task(task_id: str) -> TaskSpec`

Read and return a `TaskSpec` by ID. Raises `FileNotFoundError` if not found.

#### `update_task(task_id: str, patch: Dict[str, Any]) -> TaskSpec`

Apply a partial patch to an existing task, refresh `updated_at`, and persist. Returns the updated `TaskSpec`.

#### `list_tasks(pm_session=None) -> List[TaskSpec]`

Return all tasks sorted by filename. If `pm_session` is provided, filter to tasks matching that PM session ID.

#### `check_file_conflicts(task_id: str) -> List[str]`

Compare `owned_files` of the given task against all other tasks with status `assigned` or `in_progress`. Returns a list of conflict description strings (empty list = no conflicts).

---

### Mailbox

#### `send_message(msg: Message) -> None`

Write a `Message` to `.aya/mailbox/<to_agent>/<filename>`. Creates the directory if needed.

#### `read_inbox(agent_id: str) -> List[Message]`

Return all messages in `.aya/mailbox/<agent_id>/`, sorted by filename.

#### `clear_inbox(agent_id: str) -> int`

Delete all messages in an agent's inbox. Returns the count of deleted files.

---

### Event Log

#### `log_event(actor, event_type, data=None) -> Event`

Auto-assign the next sequence number, create an `Event`, and append it to `.aya/events.jsonl`. Returns the created `Event`.

#### `append_event(event: Event) -> None`

Low-level append of a pre-built `Event` to `events.jsonl`. Uses `fcntl.LOCK_EX` for safe concurrent writes.

#### `read_events(n=20) -> List[Event]`

Return the last `n` events from `.aya/events.jsonl`.

---

### Utility

#### `ensure_agent_dirs(pm_id: str, agent_id: str) -> None`

Create `.aya/mailbox/<pm_id>--<agent_id>/` and `.aya/logs/<agent_id>/` if they do not exist. Used when spawning a new worker.

#### `status_table() -> str`

Return a formatted multi-line string summarizing project state, PM sessions, and all tasks (ID, status, model, assigned worker, title).

---

## Factory Functions

### `create_task(...) -> TaskSpec`

```python
create_task(
    title: str,
    description: str,
    pm_session: str = "",
    depends_on: Optional[List[str]] = None,
    owned_files: Optional[List[str]] = None,
    read_files: Optional[List[str]] = None,
    acceptance_criteria: Optional[List[str]] = None,
    engine: str = "claude-agent",
    model: str = "sonnet",
) -> TaskSpec
```

Generate a new `TaskSpec` with a random `task_id` (`task-<8hex>`), current UTC timestamps for `created_at` and `updated_at`, and `branch` set to `agent/<task_id>`.

---

### `create_message(...) -> Message`

```python
create_message(
    from_agent: str,
    to_agent: str,
    msg_type: str,
    subject: str,
    body: str = "",
    data: Optional[Dict[str, Any]] = None,
) -> Message
```

Generate a new `Message` with a random `id` (`msg-<8hex>`) and current UTC timestamp.

---

### `create_pm_session(task: str) -> PMSession`

```python
create_pm_session(task: str) -> PMSession
```

Generate a new `PMSession` with a random `id` (`pm-<4hex>`) and current UTC timestamp. Status defaults to `"running"`.

---

## Directory Layout

```
<project_root>/
└── .aya/
    ├── state.json          # AyaState
    ├── config.json         # Model registry and routing rules
    ├── events.jsonl        # Append-only audit log
    ├── tasks/
    │   └── task-<id>.json  # TaskSpec per task
    ├── pms/
    │   └── pm-<id>.json    # PMSession per session
    ├── mailbox/
    │   └── <agent_id>/     # Inbox directory per agent
    │       └── <ts>-<from>-<type>.json
    ├── board/              # Shared context files (free-form)
    ├── checkpoints/        # Snapshot files
    ├── worktrees/          # Git worktree roots
    └── logs/
        └── <agent_id>/     # Per-agent log files
```

Global registry at `~/.aya-registry.json` maps project directories to their PM sessions for cross-project discovery.
