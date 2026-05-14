[English](README.md) | **中文**

# AYA — Agent Your Agent

> 管理其他 Agent 的 Agent。

AYA 将你的 [Claude Code](https://claude.com/claude-code) 会话转变为一个 **Project Manager**，它能分解任务、为每个子任务选择最合适的模型，并通过文件系统协议协调并行 Worker。

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

## 安装

**一键安装**（将 skill 和代码复制到 `~/.claude/skills/aya/`）：

```bash
git clone https://github.com/kuangren777/agent-your-agent.git && cd agent-your-agent && ./install.sh
```

**验证** — 重启 Claude Code（或执行 `/reload-plugins`），然后输入：
```
/aya
```
你应该会看到 AYA 进入 PM 模式并等待任务。

---

## 使用方法

### 启动 AYA

```
/aya "Build a Python calculator with add, subtract, multiply, divide — each in its own module, with tests"
```

或先进入 PM 模式，再输入任务：
```
/aya
> Build a calculator with four operations
```

激活后，**你发送的每条消息都会经过 AYA 的多 Agent 流水线处理**，直到你说 "exit AYA"。

### 接下来会发生什么

AYA 自动执行以下步骤：

1. **初始化** 项目目录中的 `.aya/` 工作空间
2. **分析** 你的需求，并写入 `.aya/board/requirements.md`
3. **分解** 为子任务，并声明文件归属：
   ```
   task-001: Implement add/subtract    → owned_files: [src/basic.py]
   task-002: Implement multiply/divide → owned_files: [src/advanced.py]
   task-003: Write tests               → owned_files: [tests/]
   ```
4. **路由** 每个任务到最合适的模型（参见 [Model Routing](#model-routing)）
5. **并行启动 Worker** — 没有文件冲突的任务同时运行
6. **监控** 进度，通过 `.aya/mailbox/` 消息传递
7. **合并** 所有 Worker 分支并运行集成测试
8. **汇报** 最终结果和总费用

### 发送后续指令

AYA 运行期间，你可以随时输入：
```
> Add input validation to all endpoints
> The auth module needs JWT, not session-based
> What's the current status?
> Show me the cost breakdown
```

### 退出 AYA

```
> Exit AYA
```

---

## Model Routing

AYA 会为每个任务选择能够胜任的最低成本模型：

| 任务类型 | Model | SWE-bench | Cost ($/M output) | Engine |
|---------|-------|-----------|-------------------|--------|
| 架构设计 / 调试 | Claude Opus 4.7 | 87.6% | $25 | `Agent(model="opus")` |
| 复杂重构（>5 个文件） | Claude Opus 4.7 | 87.6% | $25 | `Agent(model="opus")` |
| 标准实现 | Deepseek-v4-pro | 80.6% | **$3.48** | `claude --model deepseek-v4-pro` |
| 代码审查 | Claude Sonnet 4.6 | 79.6% | $15 | `Agent(model="sonnet")` |
| 测试 / 样板代码 | GPT-5.5 | 83% | $30 | `codex exec -m gpt-5.5` |
| 简单编辑 | Deepseek-v4-pro | 80.6% | **$3.48** | `claude --model deepseek-v4-pro` |

**成本优先级**：Deepseek ($3.48) > Haiku ($5) > Sonnet ($15) > Opus ($25) > GPT-5.5 ($30)

模型配置在 `.aya/config.json` 中，随时可以添加新模型：
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

## 并行安全

每个任务声明它将修改的文件（`owned_files`）和只读的文件（`read_files`）。AYA 强制执行以下规则：

- **没有两个并行 Worker 共享同一个 `owned_file`** — 在启动前检测冲突
- **Worker 在隔离的 git worktree 中运行** — 物理文件系统隔离
- **`board/` 对 Worker 只读** — 只有 PM/TL 可写入共享上下文

```
task-001: owned_files: [src/auth.py]     ← 可以并行运行
task-002: owned_files: [src/api.py]      ← 可以并行运行
task-003: owned_files: [src/auth.py]     ← 阻塞，直到 task-001 完成
```

---

## 文件系统协议

所有 Agent 通信使用 `.aya/` 目录下的 JSON 文件：

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

### 消息格式

Worker 通过向 mailbox 写入 JSON 文件来通信：
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

## 多 PM 会话

多个 AYA 会话可以在同一项目上运行，互不冲突：

```
Session 1: /aya "Build feature A"  → PM pm-a3f2, workers in mailbox/pm-a3f2--*
Session 2: /aya "Build feature B"  → PM pm-b7e1, workers in mailbox/pm-b7e1--*
```

每个 PM 拥有独立的 mailbox 命名空间、任务集和 Worker 池。共享上下文存放在 `board/` 中。

全局注册表 `~/.aya-registry.json` 追踪所有项目及其活跃的 PM 会话。

---

## CLI 工具

AYA 包含 PM 通过 Bash 调用的 Python 工具：

```bash
# 初始化工作空间并注册 PM 会话
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace init --pm-session --task "your task"

# 任务管理
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace write-task '{"task_id":"task-001",...}'
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace list-tasks
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace update-task task-001 '{"status":"done"}'

# 在并行启动前检查文件冲突
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace check-file-conflicts task-001

# 读取 Agent 消息
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace read-inbox pm-a3f2

# 查看状态
PYTHONPATH=~/.claude/skills/aya python3 -m aya.workspace status
```

---

## 底层工作原理

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

## 开发

```bash
# 运行测试
PYTHONPATH=src python3 -m pytest tests/ -v

# 测试 workspace CLI
PYTHONPATH=src python3 -m aya.workspace init --pm-session --task "test"
PYTHONPATH=src python3 -m aya.workspace status
```

## 许可证

MIT
