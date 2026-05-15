[English](README.md) | **中文**

# AYA — Agent Your Agent

> 管理其他 Agent 的 Agent。

AYA 将你的 [Claude Code](https://claude.com/claude-code) 或 [Codex](https://platform.openai.com/docs/guides/codex) 会话转变为一个 **Project Manager**，它能分解任务、为每个子任务选择最合适的模型，并通过文件系统协议协调并行 Worker。

```
You: /aya "Build a REST API with user auth, item CRUD, and tests"

AYA (PM):
  1. 分解为 4 个任务
  2. 路由: auth → Claude Opus, CRUD → Deepseek, tests → GPT-5.5
  3. 并行启动 3 个 Worker（文件冲突安全）
  4. 通过 ~/.aya/runtime/ 下的 mailbox 收集结果
  5. 合并分支，运行集成测试
  6. 汇报: "Done. 4 tasks, 3 workers, $0.42 total cost."
```

---

## 安装

### 方式一：一键安装

在终端或 Claude Code 中粘贴：
```bash
git clone https://github.com/kuangren777/agent-your-agent.git /tmp/aya-install && /tmp/aya-install/install.sh && rm -rf /tmp/aya-install
```

安装器会：
1. 安装核心代码到 `~/.aya/src/`
2. 安装 Claude Code skill 到 `~/.claude/skills/aya/`
3. 安装 Codex 指令到 `~/.codex/`
4. 引导你配置模型（API Key、Base URL）

### 方式二：手动安装

```bash
git clone https://github.com/kuangren777/agent-your-agent.git
cd agent-your-agent
./install.sh
```

### 安装目录结构

```
~/.aya/                          核心（Python 代码 + 配置）
├── src/aya/                     Python 包
├── models.json                  模型配置（keys, URLs）
├── runtime/                     各项目协调数据
└── registry.json                项目注册表

~/.claude/skills/aya/SKILL.md    Claude Code skill 集成
~/.codex/instructions.md         Codex 集成
```

### 验证

Claude Code：输入 `/aya` — 应该看到 PM 模式激活。

Codex：在 prompt 中提到 "aya"。

### 更新

```bash
PYTHONPATH=~/.aya/src python3 -m aya.workspace self-update
```

### 重新配置模型

```bash
PYTHONPATH=~/.aya/src python3 -m aya.workspace setup
```

---

## 使用方法

### 启动 AYA

```
/aya "用 Python 实现一个计算器，加减乘除各一个模块，带测试"
```

或先进入 PM 模式，再输入任务：
```
/aya
> 实现一个 REST API
```

激活后，**你发送的每条消息都会经过 AYA 的多 Agent 流水线处理**，直到你说 "退出 AYA"。

### AYA 的工作流程

AYA 自动执行：

1. **初始化** `~/.aya/runtime/<hash>/` 协调目录 + 项目内 `.aya-worktrees/` 工作树
2. **分析** 需求，写入 `board/requirements.md`
3. **分解** 为子任务，声明文件归属：
   ```
   task-001: 实现 add/subtract    → owned_files: [src/basic.py]
   task-002: 实现 multiply/divide → owned_files: [src/advanced.py]
   task-003: 写测试               → owned_files: [tests/]
   ```
4. **路由** 每个任务到最合适的模型（参见[模型路由](#模型路由)）
5. **并行启动 Worker** — 无文件冲突的任务同时运行，每个 Worker 在独立 git worktree 中
6. **监控** 进度，通过 `~/.aya/runtime/<hash>/mailbox/` 通信
7. **PM 串行合并** 各 Worker 分支，运行集成测试
8. **清理** worktrees，**汇报** 结果和总费用

### 发送后续指令

AYA 运行期间，你可以随时输入：
```
> 给所有端点加上输入校验
> auth 模块要用 JWT，不要 session
> 当前状态？
> 费用明细？
```

### 退出 AYA

```
> 退出 AYA
```

---

## 模型路由

AYA 为每个任务选择能胜任的最低成本模型：

| 任务类型 | Model | SWE-bench | Cost ($/M output) | Engine |
|---------|-------|-----------|-------------------|--------|
| 架构设计 / 调试 | Claude Opus 4.7 | 87.6% | $25 | `Agent(model="opus")` |
| 复杂重构（>5 文件） | Claude Opus 4.7 | 87.6% | $25 | `Agent(model="opus")` |
| 标准实现 | Deepseek-v4-pro | 80.6% | **$3.48** | `claude -p --model deepseek-v4-pro` |
| 代码审查 | Claude Sonnet 4.6 | 79.6% | $15 | `Agent(model="sonnet")` |
| 测试 / 样板代码 | GPT-5.5 | 83% | $30 | `codex exec -m gpt-5.5` |
| 简单编辑 | Deepseek-v4-pro | 80.6% | **$3.48** | `claude -p --model deepseek-v4-pro` |

**成本优先级**：Deepseek ($3.48) > Haiku ($5) > Sonnet ($15) > Opus ($25) > GPT-5.5 ($30)

引擎自动判断：名字含 `gpt/o1/o3/o4` → Codex，含 `claude/opus/sonnet/haiku` → Agent 工具，其他 → `claude -p`。

### 添加新模型

```bash
PYTHONPATH=~/.aya/src python3 -m aya.workspace setup deepseek-v4-pro \
  --base-url https://api.deepseek.com/v1 --api-key sk-xxx
```

或运行交互式向导：`PYTHONPATH=~/.aya/src python3 -m aya.workspace setup`

配置存在 `~/.aya/models.json`。

---

## 并行安全

每个任务声明 `owned_files`（独占写入）和 `read_files`（共享读取）。AYA 强制：

- **两个并行 Worker 不共享 `owned_file`** — 启动前检测冲突
- **Worker 在独立 git worktree 中工作** — 物理隔离（`.aya-worktrees/<worker>/`）
- **Worker 只 commit，不 merge** — PM 在主仓库串行 merge
- **`board/` 对 Worker 只读** — 只有 PM/TL 写共享上下文

```
task-001: owned_files: [src/auth.py]     ← 可以并行
task-002: owned_files: [src/api.py]      ← 可以并行
task-003: owned_files: [src/auth.py]     ← 阻塞，直到 task-001 完成
```

---

## 架构

**关键设计**：协调层在仓库外，Worker worktree 在项目内。

```
~/.aya/runtime/<project-hash>/     协调层（所有 Agent 共享读写）
├── tasks/  mailbox/  board/       任务、消息、共享上下文
├── state.json  config.json        项目状态、模型配置
└── events.jsonl                   事件日志

<project>/                         主仓库（PM 读 + merge）
├── .aya → symlink → runtime       方便查看
└── .aya-worktrees/                Worker worktrees（完成后清理）
    ├── worker-T1/                 独立 git worktree
    └── worker-T2/
```

Worker prompt 接收两个绝对路径：
- `工作目录` = worktree 路径（写代码的地方）
- `通信目录` = runtime 路径（读任务、写 mailbox 的地方）

两条路径物理分离，通信不受 worktree 影响。

---

## 多 PM 会话

多个 AYA 会话可以在同一项目上运行，互不冲突：

```
Session 1: /aya "Build feature A"  → PM pm-a3f2, mailbox/pm-a3f2--*
Session 2: /aya "Build feature B"  → PM pm-b7e1, mailbox/pm-b7e1--*
```

每个 PM 拥有独立的 mailbox 命名空间、任务集和 Worker 池。全局注册表 `~/.aya/registry.json` 追踪所有项目。

---

## CLI 工具

```bash
# 初始化 + 注册 PM
PYTHONPATH=~/.aya/src python3 -m aya.workspace init --pm-session --task "your task"

# 任务管理
PYTHONPATH=~/.aya/src python3 -m aya.workspace write-task '{"task_id":"task-001",...}'
PYTHONPATH=~/.aya/src python3 -m aya.workspace list-tasks
PYTHONPATH=~/.aya/src python3 -m aya.workspace update-task task-001 '{"status":"done"}'

# 文件冲突检查
PYTHONPATH=~/.aya/src python3 -m aya.workspace check-file-conflicts task-001

# Worktree 管理
PYTHONPATH=~/.aya/src python3 -m aya.workspace create-worktree worker-T1 agent/T1
PYTHONPATH=~/.aya/src python3 -m aya.workspace cleanup-worktrees

# 消息 + 状态
PYTHONPATH=~/.aya/src python3 -m aya.workspace read-inbox pm-a3f2
PYTHONPATH=~/.aya/src python3 -m aya.workspace status

# 模型管理
PYTHONPATH=~/.aya/src python3 -m aya.workspace setup
PYTHONPATH=~/.aya/src python3 -m aya.workspace models

# 更新 + 版本
PYTHONPATH=~/.aya/src python3 -m aya.workspace self-update
PYTHONPATH=~/.aya/src python3 -m aya.workspace version
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
