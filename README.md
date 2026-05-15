**English** | [中文](README_CN.md)

# AYA — Agent Your Agent

> Multi-model PM that decomposes your task, picks the right agent for each piece, and ships — while you watch.

## Why AYA

- **5-model routing, cost-aware.** Opus for architecture, Deepseek for CRUD at $3.48/M output, GPT-5.5 for tests at $30/M, Sonnet for review, Haiku for trivial edits. The right model for each sub-task, enforced by a routing table — not guesswork.
- **Zero extra cost for Claude models.** `Agent(model="opus")` uses your Claude Code subscription. Separate API billing only applies to Deepseek and GPT via `claude -p` and `codex exec`.
- **File-system protocol, no daemon.** All coordination is JSON files under `~/.aya/runtime/<hash>/`. No server to run, no queue to configure. Works anywhere Python 3 and git exist.
- **Parallel-safe by design.** Each task declares `owned_files`. AYA checks for conflicts before spawning. Workers run in isolated git worktrees — physical filesystem separation prevents races.
- **PM mode persists across context compression.** A `UserPromptSubmit` hook injects a PM reminder into every few prompts, so the model stays in PM mode even after long sessions compress the context window.
- **One-step spawn.** `spawn-worker TASK_ID` reads the task's model and engine, creates the worktree, writes the prompt, and returns the exact command to paste into `Agent()` or `Bash()`.
- **Dual coordination modes.** Sub-agent + board for independent tasks (fire-and-forget, cheap). Teammate mode for tasks that need to negotiate interfaces at runtime (bidirectional `SendMessage`).

```
You: /aya "Build a REST API with user auth, CRUD, and tests"

AYA (PM):
  Explore codebase → design approach → ask for approval
  Decompose into 4 tasks:
    task-001: auth module      → Claude Opus  (Agent tool, $0 extra)
    task-002: CRUD endpoints   → Deepseek     (claude -p, $3.48/M)
    task-003: tests            → GPT-5.5      (codex exec, $30/M)
    task-004: docs             → Deepseek     (claude -p, $3.48/M)
  Spawn workers in parallel (file-conflict checked)
  Collect via .aya/mailbox/ → merge branches → run integration tests
  Report: "Done. 4 tasks, 3 workers, $0.42 total."
```

---

## Install

### One-liner

```bash
git clone https://github.com/kuangren777/agent-your-agent.git /tmp/aya-install \
  && /tmp/aya-install/install.sh \
  && rm -rf /tmp/aya-install
```

### Development install (symlinks, edits take effect immediately)

```bash
git clone https://github.com/kuangren777/agent-your-agent.git
cd agent-your-agent
./install.sh --dev
```

### What gets installed

```
~/.aya/
├── src/aya/           Python package (workspace, models, hooks)
├── models.json        Model config (API keys, base URLs)
├── runtime/           Per-project coordination state
│   └── <hash>/        tasks/, mailbox/, board/, events.jsonl
└── registry.json      Cross-project PM session registry

~/.claude/skills/aya/SKILL.md    Claude Code /aya skill
~/.claude/settings.json          UserPromptSubmit hook added
~/.codex/instructions.md         Codex integration
```

### Verify

In Claude Code, type `/aya` — PM mode activates. The hook confirmation:

```bash
grep -q "aya.hooks" ~/.claude/settings.json && echo "Hook OK" || echo "Hook missing"
```

### Update

```bash
PYTHONPATH=~/.aya/src python3 -m aya.workspace self-update
```

### Reconfigure models

```bash
PYTHONPATH=~/.aya/src python3 -m aya.workspace setup
```

---

## How It Works

AYA follows a 7-step pipeline every time you give it a task:

**1. Init** — `workspace init` creates `~/.aya/runtime/<hash>/` and registers a PM session (e.g. `pm-a3f2`).

**2. Plan (Explore → Design → Approve)** — PM launches 1–3 `Explore` sub-agents in parallel to read the codebase, synthesizes findings into an approach, writes it to `board/plan.md`, and asks for your approval via `AskUserQuestion`. Iterates until you say yes.

**3. Decompose** — PM converts the plan's task table into `TaskSpec` JSON files: `task_id`, `owned_files`, `read_files`, `acceptance_criteria`, `model`, `engine`, `depends_on`.

**4. Spawn** — For each task with no blocking dependencies and no file conflicts, PM spawns a worker. Claude workers → `Agent(run_in_background=true)`. Deepseek workers → `claude -p --model deepseek-v4-pro` via `Bash`. GPT workers → `codex exec -m gpt-5.5` via `Bash`. The `spawn-worker` command handles all of this automatically.

**5. Monitor** — Workers write completion or question messages to `mailbox/pm-xxxx/`. PM reads with `read-inbox`. Failed workers get re-spawned with the fallback model.

**6. Merge** — PM merges worker branches into the dev branch sequentially, resolves conflicts, runs the verification command from `plan.md`. Cleans up worktrees.

**7. Report** — Task table, verification results, cost breakdown by model.

---

## Model Routing

### Billing rules

| Model family | Engine | Billing | How PM spawns |
|---|---|---|---|
| Claude (opus / sonnet / haiku) | `claude-agent` | Claude Code subscription — no extra cost | `Agent(model="opus")` tool call |
| Deepseek, Qwen, Gemini, etc. | `claude-cli` | External API key charged per token | `claude -p --model <name>` via Bash |
| GPT-5.5, o3, o4-mini | `codex` | OpenAI API key charged per token | `codex exec -m <name>` via Bash |

`claude -p` is billed separately from Claude Code even for Claude models. AYA never uses `claude -p` for opus/sonnet/haiku — always the Agent tool.

### Model strengths

| Model | SWE-bench | Cost ($/M output) | Best at |
|---|---|---|---|
| Claude Opus 4.7 | 87.6% | included | Architecture, cross-module debugging, hard multi-file reasoning |
| Claude Sonnet 4.6 | 79.6% | included | Refactoring, code review, balanced quality |
| Claude Haiku 4.5 | ~55% | included | Simple edits, formatting, classification |
| Deepseek-v4-pro | 80.6% | **$3.48** | Standard implementation, CRUD, boilerplate, docs |
| GPT-5.5 | 83.0% | $30.00 | Test generation, thorough coverage, agentic tasks |

### Routing table

| Task type | Primary | Fallback |
|---|---|---|
| Architecture / system design | opus | sonnet |
| Complex debugging (cross-module) | opus | sonnet |
| Complex refactoring (>5 files) | sonnet | deepseek-v4-pro |
| Code review | sonnet | deepseek-v4-pro |
| Standard implementation (CRUD, features) | **deepseek-v4-pro** | sonnet |
| Test generation | **gpt-5.5** | deepseek-v4-pro |
| Boilerplate / scaffolding | deepseek-v4-pro | haiku |
| Documentation | deepseek-v4-pro | haiku |
| Simple edits / config | haiku | deepseek-v4-pro |

PM calls `route-model` before writing each TaskSpec:

```bash
PYTHONPATH=~/.aya/src python3 -m aya.workspace route-model implementation
# → {"model": "deepseek-v4-pro", "engine": "claude-cli", "fallback": "claude-sonnet", ...}
```

If PM overrides the recommendation it must log the reason:

```bash
PYTHONPATH=~/.aya/src python3 -m aya.workspace log-event \
  '{"actor":"pm","event_type":"routing.override","data":{"task_id":"task-002","recommended":"deepseek-v4-pro","actual":"claude-sonnet","reason":"requires understanding complex type system"}}'
```

---

## Communication Modes

### Mode A: Sub-agent + Board (default)

Workers are fire-and-forget. PM hardcodes all interface contracts in `board/` before spawning. Workers write completion reports to `mailbox/pm-xxxx/` on finish.

Use when: files are disjoint, or workers share only read-only context.

```bash
# PM writes prompt, pipes to spawn-worker — gets back the exact command to run
cat <<'PROMPT' | PYTHONPATH=~/.aya/src python3 -m aya.workspace spawn-worker task-002
You are AYA Worker (worker-task-002)...
PROMPT
# Output: {"type": "bash", "command": "cd ... && claude -p ..."}
# or:      {"type": "agent", "command": {...}}  ← paste into Agent() tool call
```

### Mode B: Teammate (interface negotiation)

Workers join a Claude Code team and communicate with `SendMessage`. PM creates the team, spawns workers as teammates, then waits for "task done" messages to `team-lead`.

Use when: two workers must agree on a shared type, API schema, or function signature at runtime.

```
TeamCreate(team_name="aya-pm-a3f2")
Agent(name="auth-worker", team_name="aya-pm-a3f2", ...)  ← defines auth interface
Agent(name="api-worker",  team_name="aya-pm-a3f2", ...)  ← consumes auth interface
→ workers negotiate via SendMessage, no PM involvement
TeamDelete(team_name="aya-pm-a3f2")  ← cleanup on completion
```

### Decision tree

```
Tasks A and B running in parallel:
  ├── Files disjoint, no shared interfaces    → Sub-agent + Board
  ├── A reads B's output files                → Sequential (depends_on), not parallel
  ├── Both define a shared API/type           → Teammate
  └── A defines interface, B consumes it      → Teammate
```

---

## Parallel Safety

Three mechanisms prevent write conflicts between concurrent workers:

**1. `owned_files` declarations.** Each TaskSpec lists every file the worker will modify. AYA checks for overlap before spawning:

```bash
PYTHONPATH=~/.aya/src python3 -m aya.workspace check-file-conflicts task-003
# CONFLICTS:
#   task-001 (worker-task-001): ['src/auth.py']
```

Workers with conflicts are marked `blocked` and queued until the conflicting worker completes.

**2. Git worktree isolation.** Each worker operates in its own worktree at `<project>/.aya-worktrees/worker-<task_id>/` on a dedicated branch `agent/<task_id>`. The main repo and other worktrees are physically separate directories.

**3. Board is read-only for workers.** Only PM writes to `board/`. Workers read shared context from `board/` but never write to it (except writing their own `board/interface-<task_id>.md` for downstream workers to consume).

---

## Architecture

The coordination layer lives outside the project repo so worktrees don't interfere with communication:

```
~/.aya/runtime/<project-hash>/     ← coordination (tasks, mailbox, board, events)
  tasks/task-001.json              TaskSpec: model, engine, owned_files, status
  tasks/task-002.json
  pms/pm-a3f2.json                 PM session record
  mailbox/pm-a3f2/                 PM inbox (workers write here on completion)
  mailbox/pm-a3f2--worker-001/     Worker inbox (PM writes questions/directives here)
  board/requirements.md            User requirements (written by PM)
  board/plan.md                    Approved implementation plan
  board/interface-task-001.md      Interface definition written by worker-001
  events.jsonl                     Append-only audit log (all spawn/complete/cost events)
  logs/worker-task-001/
    prompt.md                      Full worker prompt (written by spawn-worker)
    result.json                    Worker output (for claude-cli workers)
  .hook_prompt_count               Hook counter for reminder frequency

<project>/.aya-worktrees/          ← worker git worktrees (deleted after merge)
  worker-task-001/                 Branch: agent/task-001
  worker-task-002/                 Branch: agent/task-002

<project>/.aya                     ← symlink → ~/.aya/runtime/<hash>/ (convenience)
```

### PM mode persistence

The `UserPromptSubmit` hook (`~/.aya/src/aya/hooks.py`) is called by Claude Code on every user message. It checks if a PM session is active for the current working directory and injects a reminder into `additionalContext`:

- Prompt 1: full reminder (~200 tokens) with workflow steps
- Prompts 2–3: skipped
- Prompt 4 (and every 3rd after): sparse reminder (~50 tokens)
- Every 5th reminder: full reminder again

This keeps PM mode alive through context compression without flooding the context window.

---

## CLI Reference

All commands use `PYTHONPATH=~/.aya/src python3 -m aya.workspace <command>`.

### Workspace

| Command | Description |
|---|---|
| `init [--pm-session] [--name N] [--task T]` | Initialize runtime dir + optionally register PM session |
| `runtime-dir` | Print the runtime directory path for the current project |
| `status` | Show PM sessions, tasks, worktrees, total cost |
| `check-env` | Check that required CLIs (claude, codex, git) are installed |
| `version` | Print AYA version |
| `self-update` | Pull latest from GitHub and reinstall |

### PM sessions

| Command | Description |
|---|---|
| `list-pms` | List all PM sessions for the current project |

### Tasks

| Command | Description |
|---|---|
| `write-task JSON` | Write a TaskSpec (creates `tasks/<task_id>.json`) |
| `update-task TASK_ID JSON` | Patch fields on an existing task |
| `list-tasks [--pm PM_ID]` | List all tasks, optionally filtered by PM session |
| `check-file-conflicts TASK_ID` | Check if owned_files overlap with any in-progress task |

### Mailbox

| Command | Description |
|---|---|
| `send-msg JSON` | Write a Message JSON to the recipient's inbox |
| `read-inbox AGENT_ID` | Read all messages in an agent's inbox |

### Worktrees

| Command | Description |
|---|---|
| `create-worktree WORKER_ID BRANCH` | Create a git worktree for a worker |
| `remove-worktree WORKER_ID` | Remove a single worker's worktree |
| `cleanup-worktrees` | Remove all worktrees under `.aya-worktrees/` |

### Model routing and spawn

| Command | Description |
|---|---|
| `route-model TASK_TYPE` | Look up routing table, return recommended model + engine + fallback |
| `spawn-worker TASK_ID` | Read prompt from stdin, create worktree, write prompt file, return spawn command JSON |
| `spawn-command TASK_ID [--prompt-file PATH]` | Generate spawn command JSON without creating worktree (lower-level) |
| `list-models` | List all configured models with engine, availability, and routing priority |

### Model configuration

| Command | Description |
|---|---|
| `setup` | Interactive model setup wizard (API keys, base URLs) |
| `setup MODEL [--base-url URL] [--api-key KEY]` | Add a model non-interactively |
| `models` | Print configured models from `~/.aya/models.json` |
| `model-env MODEL` | Print env vars needed to use a model via `claude -p` |

### Events

| Command | Description |
|---|---|
| `log-event JSON` | Append an event to `events.jsonl` |

---

## Development

```bash
# Install in dev mode (symlinks — edits take effect without reinstalling)
./install.sh --dev

# Run tests
PYTHONPATH=src python3 -m pytest tests/ -v

# Test the CLI
PYTHONPATH=src python3 -m aya.workspace init --pm-session --task "test"
PYTHONPATH=src python3 -m aya.workspace status

# Check version
PYTHONPATH=src python3 -m aya.workspace version
```

Git hooks for automatic version bumps are installed from `githooks/` during `install.sh`.

---

## License

MIT
