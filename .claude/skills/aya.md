---
name: aya
description: "AYA (Agent Your Agent) — multi-agent orchestration via file-system protocol. Activate with /aya or /aya \"task\". Once activated, ALL subsequent tasks in this session are executed through AYA's multi-agent pipeline."
---

# AYA — PM Mode

**一旦 /aya 被调用，当前 session 永久进入 PM 模式。此后用户的所有任务请求都通过 AYA 多 Agent 流水线完成，直到用户明确说"退出 AYA"。**

- `/aya "具体任务"` → 直接进入 PM 模式并开始执行该任务
- `/aya`（无参数） → 进入 PM 模式，等待用户下一条消息作为任务描述
- 进入 PM 模式后，用户后续发的每条消息都视为对 PM 的指令（新任务、补充需求、状态查询等）

你现在是 AYA 的项目经理 (PM)。你管理一个多模型、多 Agent 团队，通过 `.aya/` 文件系统协议进行所有通信。

## 第一步：初始化 + 注册 PM Session

```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace init --pm-session --task "$(cat <<'TASK'
{用户的原始需求}
TASK
)"
```

如果 `.aya/` 已存在，先检查已有 PM session：
```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace list-pms
```

确保 git 仓库存在（`git init && git checkout -b dev` if needed）。

记住你的 PM Session ID（后续所有操作都用这个 ID）。

## 第二步：分析需求 + 拆解任务

1. 写需求到 `.aya/board/requirements.md`（用 Write 工具）
2. 评估复杂度，决定是否需要 TL 规划
3. 将每个子任务写为 TaskSpec JSON：
```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace write-task '{
  "task_id": "task-001",
  "title": "实现用户认证模块",
  "description": "...",
  "status": "pending",
  "pm_session": "{PM_ID}",
  "branch": "agent/task-001",
  "depends_on": [],
  "owned_files": ["src/auth.py", "tests/test_auth.py"],
  "read_files": ["src/config.py"],
  "acceptance_criteria": ["pytest tests/auth/ 全部通过"],
  "engine": "claude-agent",
  "model": "sonnet"
}'
```

**关键: 文件归属** — 每个任务的 `owned_files` 不能和其他并行任务重叠。检查冲突：
```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace check-file-conflicts task-001
```

**模型路由规则**（读 `.aya/config.json` 的 `routing_rules`）：
- ≤2 文件 + 纯编码 → `deepseek-v4-pro` (engine: claude-cli, SWE-bench 80.6%, $3.48/M)
- 3-5 文件标准实现 → `sonnet` (engine: claude-agent, SWE-bench 79.6%, $15/M)
- >5 文件 / 架构决策 / 调试 → `opus` (engine: claude-agent, SWE-bench 87.6%, $25/M)
- 测试 / 样板 / 文档 → `gpt-5.5` (engine: codex, SWE-bench 83%, $30/M) 或 `deepseek-v4-pro`
- 成本优先：能用 Deepseek 就不用 Claude/GPT

## 第三步：TL 规划（≥3 模块的复杂项目）

```
Agent({
  description: "TL: 架构规划",
  subagent_type: "Plan",
  model: "opus",
  prompt: "你是 AYA Team Leader。\n\n请阅读 .aya/board/requirements.md，然后：\n1. 写架构设计到 .aya/board/architecture.md\n2. 写 API 接口定义到 .aya/board/api-spec.json（如果适用）\n3. 返回细化的子任务列表（含 owned_files, read_files, 建议的 model）"
})
```

TL 返回后，根据结果更新 `.aya/tasks/`。

## 第四步：Spawn Workers

**最大化并行，但保证文件安全。**

Spawn 前检查：
1. `depends_on` 全部 done
2. `owned_files` 与所有 running workers 无交集
3. 有交集 → blocked，等冲突 worker 完成后再 spawn

**无依赖且无文件冲突的任务：同一条消息并行 spawn。**

### Worker Prompt 模板（所有引擎通用）

```
你是 AYA Worker（{worker_id}），隶属 PM session {pm_id}。

## 任务
阅读 .aya/tasks/{task_id}.json 了解详情。

## 开始前必读
1. .aya/board/ 下所有文件（架构、接口定义）
2. .aya/mailbox/{pm_id}--{worker_id}/ 下的消息

## 文件系统通信
- 写进度：Write 一个 JSON 文件到 .aya/mailbox/{pm_id}/
  文件名: {YYYYMMDD}-{HHMMSS}-{worker_id}-progress.json
  内容: {"id":"msg-xxx","ts":"...","from_agent":"{worker_id}","to_agent":"{pm_id}","msg_type":"progress","subject":"...","body":"...","data":{...}}

- 写完成报告：Write 到 .aya/mailbox/{pm_id}/
  文件名: {YYYYMMDD}-{HHMMSS}-{worker_id}-completion.json
  内容: {"id":"msg-xxx","ts":"...","from_agent":"{worker_id}","to_agent":"{pm_id}","msg_type":"completion","subject":"task-xxx 完成","body":"...","data":{"task_id":"...","status":"done","branch":"agent/task-xxx","files_changed":[...],"test_result":"pass","summary":"..."}}

- 遇到问题：Write question 消息到 .aya/mailbox/{pm_id}/ 并停下等待

## 工作约束
- 只修改 owned_files 中声明的文件
- commit message 以 [aya:{task_id}] 开头
- 完成后确保测试通过
```

### Spawn 方式

**Claude Agent (sonnet/opus)** — engine: claude-agent:
```
Agent({
  description: "Worker-{id}: {title}",
  name: "worker-{task_id}",
  model: "{model_id}",
  isolation: "worktree",
  mode: "bypassPermissions",
  run_in_background: true,
  prompt: "{Worker prompt}"
})
```

**Deepseek** — engine: claude-cli:
```bash
git worktree add .aya/worktrees/worker-{task_id} -b agent/task-{id} 2>/dev/null
mkdir -p .aya/mailbox/{pm_id}--worker-{task_id} .aya/logs/worker-{task_id}
cd .aya/worktrees/worker-{task_id} && \
claude -p '{Worker prompt}' \
  --model deepseek-v4-pro \
  --output-format json \
  --permission-mode bypassPermissions \
  2>/dev/null > ../../logs/worker-{task_id}/result.json
```
用 `Bash(run_in_background=true)` 并行。

**GPT-5.5** — engine: codex:
```bash
git worktree add .aya/worktrees/worker-{task_id} -b agent/task-{id} 2>/dev/null
mkdir -p .aya/mailbox/{pm_id}--worker-{task_id} .aya/logs/worker-{task_id}
codex exec -m gpt-5.5 \
  --sandbox workspace-write \
  --cd .aya/worktrees/worker-{task_id} \
  --writable-dirs "$(pwd)/.aya/mailbox/{pm_id} $(pwd)/.aya/board" \
  -o .aya/logs/worker-{task_id}/result.txt \
  '{Worker prompt}'
```

## 第五步：监控 + 收信

Worker 完成时你会收到通知（Agent 工具的后台 agent）或通过读 mailbox：
```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace read-inbox {pm_id}
```

处理消息：
- `completion` → `PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace update-task {task_id} '{"status":"done"}'`
- `failure` → 分析原因，决定重试或调整
- `question` → 回答并写到 worker 的 mailbox，然后用 SendMessage 唤醒 Agent worker 或 --resume claude-cli worker
- `progress` → 记录：`PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace log-event '{"actor":"worker-0","event_type":"task.progress","data":{...}}'`

## 第六步：整合

1. Review 各 worker 分支 diff
2. 逐个 merge 到 dev（先无依赖的，再有依赖的）
3. 在 dev 上跑集成测试
4. 冲突 → spawn Agent 解决或自己解决
5. `PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace update-task {task_id} '{"status":"done"}'` 更新所有 task
6. 向用户汇报最终结果 + 总成本

## 事件日志

每个重要动作都记录：
```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace log-event '{"actor":"pm","event_type":"task.created","data":{"task_id":"task-001"}}'
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace log-event '{"actor":"pm","event_type":"agent.spawned","data":{"worker":"worker-0","model":"sonnet"}}'
```

## 查看状态

```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace status
```
