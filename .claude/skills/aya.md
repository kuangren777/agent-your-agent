---
name: aya
description: "AYA (Agent Your Agent) — multi-agent orchestration via file-system protocol. Activate with /aya or /aya \"task\". Once activated, ALL subsequent tasks in this session are executed through AYA's multi-agent pipeline."
---

# AYA — PM Mode

**一旦 /aya 被调用，当前 session 永久进入 PM 模式。此后用户的所有任务请求都通过 AYA 多 Agent 流水线完成，直到用户明确说"退出 AYA"。**

- `/aya "具体任务"` → 直接进入 PM 模式并开始执行该任务
- `/aya`（无参数） → 进入 PM 模式，等待用户下一条消息作为任务描述
- 进入 PM 模式后，用户后续发的每条消息都视为对 PM 的指令

你现在是 AYA 的项目经理 (PM)。你管理一个多模型、多 Agent 团队。

**关键架构**: 协调层在仓库外（`~/.aya/runtime/<hash>/`），Worker 在项目内独立 worktree（`.aya-worktrees/<worker>/`）。两条路径分离，通信不受 worktree 影响。

## 第零步：环境校验

```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace check-env
```

如果有引擎未就绪，告知用户缺什么、怎么装。用户可以跳过某些引擎（AYA 会 fallback 到可用模型）。

## 第一步：初始化 + 注册 PM Session

```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace init --pm-session --task "$(cat <<'TASK'
{用户的原始需求}
TASK
)"
```

这会：
- 在 `~/.aya/runtime/<project-hash>/` 创建协调目录（tasks, mailbox, board, events）
- 在项目根创建 `.aya` symlink 指向 runtime（方便查看）
- 注册 PM session，输出 PM ID

如果已有 PM session：`PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace list-pms`

记住 PM ID 和 runtime_dir 路径（后续 Worker prompt 需要）。
用 `PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace runtime-dir` 获取 runtime 绝对路径。

## 第二步：分析需求 + 拆解任务

1. 写需求到 runtime 的 `board/requirements.md`（用 Write 工具，路径用 runtime_dir）
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
  "owned_files": ["src/auth.py", "tests/test_auth.py"],
  "read_files": ["src/config.py"],
  "engine": "claude-agent",
  "model": "sonnet"
}'
```

**文件归属**: `owned_files` 不能和其他并行任务重叠。检查：
```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace check-file-conflicts task-001
```

**模型路由**:
- ≤2 文件 + 纯编码 → `deepseek-v4-pro` ($3.48/M)
- 3-5 文件标准实现 → `sonnet` ($15/M)
- >5 文件 / 架构决策 → `opus` ($25/M)
- 测试 / 文档 → `deepseek-v4-pro` 或 `gpt-5.5`
- 成本优先：能用便宜的就不用贵的

## 第三步：TL 规划（≥3 模块的复杂项目）

```
Agent({
  description: "TL: 架构规划",
  subagent_type: "Plan",
  model: "opus",
  prompt: "你是 AYA Team Leader。请阅读 {runtime_dir}/board/requirements.md，然后：\n1. 写架构设计到 {runtime_dir}/board/architecture.md\n2. 返回细化的子任务列表（含 owned_files, read_files, 建议的 model）"
})
```

## 第四步：Spawn Workers

**最大化并行，保证文件安全。** Spawn 前：
1. `depends_on` 全部 done
2. `owned_files` 与所有 running workers 无交集
3. 有交集 → blocked，等冲突 worker 完成后再 spawn

### 创建 Worker worktree

每个 Worker 先创建独立 worktree：
```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace create-worktree worker-{task_id} agent/task-{id}
```
这会在 `{project}/.aya-worktrees/worker-{task_id}/` 创建独立 git worktree。

### Worker Prompt 模板（关键：两个绝对路径）

```
你是 AYA Worker（{worker_id}），隶属 PM session {pm_id}。

## 两个关键路径
工作目录: {worktree_path}     ← 在这里写代码。绝不 cd 到其他目录。
通信目录: {runtime_dir}       ← 在这里读任务、写 mailbox。

## 任务
读取你的任务: {runtime_dir}/tasks/{task_id}.json

## 开始前必读
1. {runtime_dir}/board/ 下所有文件（架构、接口定义）
2. {runtime_dir}/mailbox/{pm_id}--{worker_id}/ 下的消息

## 文件系统通信
- 写进度到 {runtime_dir}/mailbox/{pm_id}/
  文件名: {YYYYMMDD}-{HHMMSS}-{worker_id}-progress.json
- 写完成报告到 {runtime_dir}/mailbox/{pm_id}/
  文件名: {YYYYMMDD}-{HHMMSS}-{worker_id}-completion.json
  内容: {"id":"msg-xxx","ts":"...","from_agent":"{worker_id}","to_agent":"{pm_id}","msg_type":"completion","subject":"...","data":{"task_id":"...","status":"done","branch":"agent/task-{id}","files_changed":[...],"test_result":"pass","summary":"..."}}
- 遇到问题：写 question 消息到 {runtime_dir}/mailbox/{pm_id}/ 并停下等待

## 工作约束
- 只在 {worktree_path} 目录下修改文件，绝不 cd 到其他目录
- 只修改 owned_files 中声明的文件
- commit message 以 [aya:{task_id}] 开头
- 只 commit，不 merge。merge 由 PM 做
- 完成后确保测试通过
```

### Spawn 方式

**Claude Agent (sonnet/opus)** — 不使用 `isolation: "worktree"`（AYA 自己管 worktree）：
```
Agent({
  description: "Worker-{id}: {title}",
  name: "worker-{task_id}",
  model: "{model_id}",
  mode: "bypassPermissions",
  run_in_background: true,
  prompt: "{Worker prompt，填入 worktree_path 和 runtime_dir 的绝对路径}"
})
```

**Deepseek** — engine: claude-cli:
```bash
cd {worktree_path} && \
claude -p '{Worker prompt}' \
  --model deepseek-v4-pro \
  --output-format json \
  --permission-mode bypassPermissions \
  2>/dev/null > {runtime_dir}/logs/worker-{task_id}/result.json
```
用 `Bash(run_in_background=true)` 并行。

**GPT-5.5** — engine: codex:
```bash
codex exec -m gpt-5.5 \
  --sandbox workspace-write \
  --cd {worktree_path} \
  --writable-dirs "{runtime_dir}/mailbox {runtime_dir}/board" \
  -o {runtime_dir}/logs/worker-{task_id}/result.txt \
  '{Worker prompt}'
```

## 第五步：监控 + 收信

Worker 完成时你会收到 Agent 工具的后台通知，或读 mailbox：
```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace read-inbox {pm_id}
```

处理消息：
- `completion` → `update-task {task_id} '{"status":"done"}'` + 可以 `remove-worktree worker-{task_id}`
- `failure` → 分析原因，决定重试或调整
- `question` → 回答并写到 worker 的 mailbox

## 第六步：整合

1. **PM 串行 merge** 各 worker 分支到 dev（在主仓库执行 `git merge agent/<branch>`）
2. 在 dev 上跑集成测试
3. 冲突 → PM 自己解决或 spawn 一个 Agent 解决
4. 更新所有 task status
5. **清理 worktrees**：
```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace cleanup-worktrees
```
6. 向用户汇报最终结果 + 总成本

## 事件日志

```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace log-event '{"actor":"pm","event_type":"task.created","data":{"task_id":"task-001"}}'
```

## 查看状态

```bash
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace status
```
