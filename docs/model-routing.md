# AYA Model Routing

## Overview

AYA routes each sub-task to the cheapest model that can handle it. When the PM decomposes a task, it assigns a `task_type` to each sub-task and looks up the routing table to find the best match. The guiding principle is **cost-first**: among models that meet the capability requirement, AYA always prefers the lowest output cost per million tokens. More expensive models (Opus, GPT-5.5) are reserved for work that genuinely needs their higher benchmark scores or specialized capabilities.

Each model is backed by one of three engines that determine how a worker process is spawned. The PM never runs model inference directly — it only orchestrates workers and reads their results from `.aya/mailbox/`.

---

## Available Models

| Model | Engine | SWE-bench | Input ($/M tok) | Output ($/M tok) | Context | Speed |
|---|---|---|---|---|---|---|
| Claude Opus 4.7 | `claude-agent` | 87.6% | $5.00 | $25.00 | 1M | slow |
| Claude Sonnet 4.6 | `claude-agent` | 79.6% | $3.00 | $15.00 | 1M | fast |
| Claude Haiku 4.5 | `claude-agent` | ~55% | $1.00 | $5.00 | 200K | fastest |
| Deepseek-v4-pro | `claude-cli` | 80.6% | $1.74 | $3.48 | 1M | fast |
| GPT-5.5 | `codex` | ~83% | $5.00 | $30.00 | 1M | medium |

**Capability tags** declared in `config.json`:

| Model | Capabilities |
|---|---|
| claude-opus | architecture, complex_refactor, debugging, multi_file |
| claude-sonnet | implementation, review, standard_coding |
| claude-haiku | classification, simple_edit, formatting, routing |
| deepseek-v4-pro | implementation, algorithm, math, coding, boilerplate |
| gpt-5.5 | implementation, testing, boilerplate, documentation, agentic |

---

## Routing Rules

The default routing matrix is stored under `routing_rules` in `.aya/config.json`. Each entry maps a `task_type` to a `prefer` model and a `fallback` model used when the preferred model is unavailable or over capacity.

| task_type | Preferred Model | Fallback |
|---|---|---|
| `architecture` | claude-opus | claude-sonnet |
| `complex_refactor` | claude-opus | deepseek-v4-pro |
| `debugging` | claude-opus | claude-sonnet |
| `implementation` | deepseek-v4-pro | claude-sonnet |
| `testing` | deepseek-v4-pro | claude-sonnet |
| `boilerplate` | deepseek-v4-pro | gpt-5.5 |
| `review` | claude-sonnet | claude-haiku |
| `documentation` | gpt-5.5 | claude-haiku |

### Cost-Priority Principle

Output tokens dominate cost for most coding tasks. The cost ladder (cheapest to most expensive per M output tokens) is:

```
Deepseek-v4-pro ($3.48) < Claude Haiku ($5) < Claude Sonnet ($15) < Claude Opus ($25) < GPT-5.5 ($30)
```

AYA applies this ordering when two models share a capability. For example, both Deepseek and Sonnet can do `implementation`, so the router picks Deepseek first and falls back to Sonnet only if Deepseek is unavailable. Opus and GPT-5.5 are never chosen for commodity work — Opus is reserved for tasks requiring multi-file reasoning or architecture-level judgment, and GPT-5.5 for documentation and agentic testing workflows where the Codex sandbox is desirable.

---

## Engine Configurations

Each model belongs to one of three engines. The engine determines how the PM spawns the worker and how results are collected.

### `claude-agent` — Agent Tool

Used by: claude-opus, claude-sonnet, claude-haiku.

The PM spawns a worker by calling the `Agent` tool with a `model` parameter, running the agent in an isolated git worktree under `.aya/worktrees/<task-id>/`. The agent has full filesystem access within that worktree and writes its completion report to `.aya/mailbox/<pm-id>/`.

```
Agent(
  model="opus",           # or "sonnet" / "haiku"
  worktree=".aya/worktrees/task-001",
  permission_mode="bypassPermissions"
)
```

Worker output is the agent's final message, which the PM parses as a JSON summary.

### `claude-cli` — Bash + claude CLI

Used by: deepseek-v4-pro.

The PM spawns a background Bash process running the `claude` CLI in non-interactive (`-p`) mode:

```bash
claude -p \
  --model deepseek-v4-pro \
  --output-format json \
  --permission-mode bypassPermissions \
  < task_prompt.txt
```

The worker is a subprocess; its stdout is captured as a JSON object. The model_id is substituted at spawn time using the template `{model_id}` from `engine_configs.claude-cli.spawn_via`.

### `codex` — Bash + codex exec

Used by: gpt-5.5.

The PM spawns a Codex worker in a sandboxed environment:

```bash
codex exec \
  -m gpt-5.5 \
  --sandbox workspace-write \
  --cd .aya/worktrees/task-002 \
  --writable-dirs .aya/mailbox/pm-a3f2 .aya/board \
  < task_prompt.txt
```

The `workspace-write` sandbox restricts writes to the worktree directory, with explicit extra permissions granted for the PM's mailbox and the shared board. This provides an additional layer of isolation compared to the claude-agent and claude-cli engines.

---

## Adding New Models

Follow these steps to register a new model in AYA:

1. **Edit `.aya/config.json`** in your project directory (or `~/.claude/skills/aya/config.json` to set a global default).

2. **Add a model entry** under the `"models"` key:

   ```json
   {
     "models": {
       "my-new-model": {
         "engine": "claude-cli",
         "model_id": "my-provider/model-name",
         "capabilities": ["implementation", "coding"],
         "swe_bench_verified": 75.0,
         "cost_input_per_mtok": 2.0,
         "cost_output_per_mtok": 6.0,
         "speed": "fast",
         "context_window": 128000,
         "file_access": "full"
       }
     }
   }
   ```

   Required fields: `engine`, `model_id`, `capabilities`, `cost_output_per_mtok`. All other fields are optional but recommended for accurate cost reporting.

3. **Add a routing rule** (optional) to direct a specific task type to the new model:

   ```json
   {
     "routing_rules": [
       {"task_type": "implementation", "prefer": "my-new-model", "fallback": "claude-sonnet"}
     ]
   }
   ```

   If you want the new model to be a fallback only, add it as the `fallback` in an existing rule without creating a new rule.

4. **Verify** by running:

   ```bash
   PYTHONPATH=src python3 -m aya.workspace status
   ```

   The model registry section will list all registered models including the new entry.

> **Note**: If you use `engine: "claude-cli"`, make sure the target model is accessible via `claude --model <model_id>`. If using `engine: "codex"`, the model must be supported by `codex exec -m`. For models accessed through third-party proxies (e.g., OpenRouter), set `model_id` to the provider's fully-qualified model string.

---

## Cost Optimization Tips

### When to use each model

**Use Deepseek-v4-pro** for the majority of implementation work. At $3.48/M output tokens it is 4x cheaper than Sonnet and 7x cheaper than Opus. Its 80.6% SWE-bench score is competitive with Sonnet, making it the default for `implementation`, `testing`, and `boilerplate` tasks.

**Use Claude Sonnet 4.6** when you need full Claude Code tool integration, code review judgment, or tasks that benefit from in-context reasoning across large files. At $15/M it costs roughly 4x Deepseek — reserve it for `review` and as the primary fallback for implementation tasks that Deepseek struggles with.

**Use Claude Opus 4.7** only for tasks that genuinely require top-tier reasoning: architecture design, debugging subtle multi-file issues, complex refactors touching more than 5 files. Its 87.6% SWE-bench score justifies the $25/M output cost for these cases, but the cost multiplier versus Deepseek is ~7x.

**Use Claude Haiku 4.5** for lightweight orchestration work: classifying task types, formatting outputs, routing decisions, simple one-line edits. At $5/M it is cheap, but its ~55% SWE-bench score means it should not handle substantive coding tasks.

**Use GPT-5.5** for documentation generation and test suites where the Codex sandbox's workspace-write isolation is a benefit. At $30/M output it is the most expensive option; only prefer it when the `codex exec` sandbox model is specifically required or when you are generating documentation that does not need real-time file access.

### Cost comparison examples

| Task | Naive choice | Optimal choice | Estimated saving |
|---|---|---|---|
| 50 CRUD endpoints | Opus ($25/M) | Deepseek ($3.48/M) | ~7x |
| Write 200 unit tests | Opus ($25/M) | Deepseek ($3.48/M) | ~7x |
| Code review (10 files) | Opus ($25/M) | Sonnet ($15/M) | ~1.7x |
| Architecture decision | Deepseek ($3.48/M) | Opus ($25/M) | quality gain, not saving |
| Format config files | Sonnet ($15/M) | Haiku ($5/M) | ~3x |

A typical project of moderate complexity (auth + CRUD + tests, ~1500 output tokens per task) costs roughly:
- All-Opus: ~$0.11 per task
- AYA-routed (default rules): ~$0.02–0.04 per task depending on mix

For a 10-task project this translates to roughly **$0.20–0.40 with AYA routing vs. $1.10 with Opus for everything**.
