[English](README.md) | **中文**

# AYA — Agent Your Agent

> 管理其他 Agent 的 Agent。

AYA 将你的 [Claude Code](https://claude.com/claude-code) 会话转变为一个 **Project Manager（PM）**，它能分解任务、为每个子任务智能选择最合适的模型，并通过文件系统协议协调并行 Worker——无需守护进程，无需消息队列。

```
You: /aya "Build a REST API with user auth, item CRUD, and tests"

AYA (PM):
  1. 分解为 4 个任务
  2. 路由: auth → Claude Opus, CRUD → Deepseek, tests → GPT-5.5
  3. 并行启动 3 个 Worker（文件冲突安全）
  4. 通过 ~/.aya/runtime/<hash>/mailbox/ 收集结果
  5. 合并分支，运行集成测试
  6. 汇报: "Done. 4 tasks, 3 workers, $0.42 total cost."
```

---

## 为什么选 AYA

- **5 模型智能路由**：每个任务分配最合适的模型——Opus 做架构设计、Deepseek 写 CRUD（仅 $3.48/M）、GPT-5.5 生成测试、Sonnet 做代码审查、Haiku 处理简单编辑。不同任务用不同模型，不是所有任务都堆给最贵的那个。
- **Claude 模型零额外成本**：通过 Agent 工具走 Claude Code 订阅（opus/sonnet/haiku），不额外计费。只有外部模型（Deepseek/GPT）才需要支付 API 费用。**`claude -p` 会单独计费，AYA 对 Claude 模型绝不使用它。**
- **文件系统协议**：无守护进程、无 REST API、无消息队列。所有通信通过 JSON 文件完成。只需 Python + git 即可运行，没有任何隐藏服务。
- **并行安全**：`owned_files` 独占声明 + git worktree 物理隔离，确保并发 Worker 安全运行，不会发生文件冲突。
- **PM 模式持久化**：UserPromptSubmit hook 在 context 压缩后仍将模型保持在 PM 身份——每隔 N 条消息自动注入 PM 模式提醒，无需手动重激活。
- **一步 spawn**：`spawn-worker` 命令自动建 worktree、写 prompt、选引擎、生成 spawn 命令。PM 只需 pipe prompt，不需要知道底层用哪个引擎或怎么调用它。
- **双通信模式**：Sub-agent（board 广播）适合独立任务，Teammate（SendMessage 实时通信）适合需要协商接口的耦合任务——PM 根据任务依赖关系自动选择。

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

### 方式三：开发模式

```bash
git clone https://github.com/kuangren777/agent-your-agent.git
cd agent-your-agent
pip install -e ".[dev]"
```

开发模式下，修改 `src/aya/` 下的代码后无需重新安装即可生效。

### 安装目录结构

```
~/.aya/                          核心（Python 代码 + 配置）
├── src/aya/                     Python 包
├── models.json                  模型配置（keys, URLs）
├── runtime/                     各项目协调数据（按项目 hash 分隔）
└── registry.json                全局项目注册表

~/.claude/skills/aya/SKILL.md    Claude Code skill 集成
~/.codex/instructions.md         Codex 集成
```

### 验证安装

Claude Code：输入 `/aya`，应该看到 PM 模式激活。

### 更新

```bash
PYTHONPATH=~/.aya/src python3 -m aya.workspace self-update
```

### 重新配置模型

```bash
PYTHONPATH=~/.aya/src python3 -m aya.workspace setup
```

---

## 工作流程

AYA 的完整流水线分 7 步：

### 第 0 步：环境检查

```bash
PYTHONPATH=~/.aya/src python3 -m aya.workspace check-env
```

检查 `claude`、`codex`、`git` 三个二进制是否就绪。缺失的引擎会跳过，AYA 自动回退到可用模型。

### 第 1 步：初始化 + 注册 PM 会话

```bash
PYTHONPATH=~/.aya/src python3 -m aya.workspace init --pm-session --task "用户描述的任务"
```

创建 `~/.aya/runtime/<project-hash>/` 协调目录，注册 PM 会话（返回 PM ID，如 `pm-a3f2`），并在项目目录建立 `.aya` 符号链接方便访问。

### 第 2 步：Plan 阶段——探索、设计、对齐（核心阶段）

**绝不跳过规划。** Plan 阶段循环三个子阶段，直到方案成熟：

**Phase A — 探索：** PM 并行 spawn 1–3 个 Explore 子 Agent，深入读取代码库，理解现有模式、可复用函数、相关测试。结果写入 `board/requirements.md`。

**Phase B — 设计：** 对复杂任务（3+ 模块或架构决策），spawn 一个 Plan Agent 设计实现方案；对简单任务（≤2 个文件），PM 直接设计。

**Phase C — 对齐：** 将方案写入 `board/plan.md`（含任务表、文件归属、模型建议、可复用代码、验证命令），通过 `AskUserQuestion` 向用户确认。有反馈则循环回 A 或 B。

### 第 3 步：任务分解——转换为 TaskSpec

用户批准方案后，PM 将 plan.md 中的任务表转换为 TaskSpec JSON，每个任务一个文件：

```bash
PYTHONPATH=~/.aya/src python3 -m aya.workspace write-task '{
  "task_id": "task-001",
  "title": "实现用户认证模块",
  "description": "## 目标\n实现 JWT 中间件\n\n## 验收标准\n- pytest tests/test_auth.py 全部通过\n- 认证失败返回 401",
  "owned_files": ["src/auth.py", "tests/test_auth.py"],
  "read_files": ["src/config.py", "src/utils/validators.py"],
  "acceptance_criteria": ["pytest tests/test_auth.py 通过", "401 on auth failure"],
  "engine": "claude-agent",
  "model": "claude-opus"
}'
```

写完每个任务后立即检查文件冲突：

```bash
PYTHONPATH=~/.aya/src python3 -m aya.workspace check-file-conflicts task-001
```

### 第 4 步：Spawn Workers——选择模式，最大化并行

对每个任务，PM 先调用 `route-model` 获取推荐模型，再用 `spawn-worker` 一步完成 worktree 创建、prompt 写入、spawn 命令生成：

```bash
# 获取路由建议
PYTHONPATH=~/.aya/src python3 -m aya.workspace route-model implementation

# 一步 spawn（prompt 从 stdin 读取）
cat <<'PROMPT' | PYTHONPATH=~/.aya/src python3 -m aya.workspace spawn-worker task-001
You are AYA Worker...
PROMPT
```

`spawn-worker` 输出 JSON，PM 根据 `type` 字段选择调用方式：
- `type: "agent"` → 将 `command` 粘贴到 `Agent()` 工具调用（Claude 订阅，零额外成本）
- `type: "bash"` → 将 `command` 粘贴到 `Bash(run_in_background=true)`（Deepseek/GPT，按量计费）

### 第 5 步：监控 + 接收消息

```bash
PYTHONPATH=~/.aya/src python3 -m aya.workspace read-inbox pm-a3f2
```

Worker 完成后写 completion 消息到 PM 的 mailbox。PM 处理：
- `status=done` → 更新任务状态，解锁被阻塞的下游任务，清理 worktree
- `status=fail` → 分析原因，用更强模型重试或调整任务描述后重新 spawn
- `question` → 如果 PM 能回答则直接回复；否则通过 `AskUserQuestion` 向用户询问

### 第 6 步：合并 + 验证

PM 串行将 Worker 分支合并到主分支：

```bash
cd {project_dir} && git merge agent/{task_id} --no-edit
```

运行 plan.md 中的验证命令（如 `pytest tests/ -v`），如有失败则 spawn fix worker。最后清理所有 worktree：

```bash
PYTHONPATH=~/.aya/src python3 -m aya.workspace cleanup-worktrees
```

### 第 7 步：汇报

```
## 完成报告

**任务**: 构建带认证的 REST API
**结果**: 成功

| 任务 | 状态 | 模型 | 修改文件 |
|------|------|------|----------|
| task-001: 认证模块 | done | claude-opus | src/auth.py, tests/test_auth.py |
| task-002: CRUD 端点 | done | deepseek-v4-pro | src/api.py |

**验证**: pytest 全部通过
**费用**: Claude 模型（订阅包含）+ Deepseek $0.12 = $0.12 总计
```

---

## 模型路由

AYA 为每个任务选择能胜任的最低成本模型。

### 计费规则（关键）

| 模型系列 | 引擎 | 计费方式 | Spawn 方式 |
|---------|------|---------|-----------|
| Claude（opus/sonnet/haiku） | claude-agent | **Claude Code 订阅包含，免额外费用** | 仅用 Agent 工具 |
| Deepseek、Qwen、Gemini 等 | claude-cli | 外部 API Key 按量计费 | `claude -p --model <name>` |
| GPT、o1、o3、o4 | codex | OpenAI API Key 按量计费 | `codex exec` |

**`claude -p` 对 Claude 模型单独计费。** AYA 对 opus/sonnet/haiku 绝不使用 `claude -p`，始终走 Agent 工具。

### 模型能力与成本

| 模型 | SWE-bench | 输出成本 ($/M) | 引擎 | 擅长 |
|------|-----------|---------------|------|------|
| Claude Opus 4.7 | 87.6% | 订阅包含 | claude-agent | 架构设计、复杂调试、多文件推理 |
| Claude Sonnet 4.6 | 79.6% | 订阅包含 | claude-agent | 重构、代码审查、质量敏感任务 |
| Claude Haiku 4.5 | ~55% | 订阅包含 | claude-agent | 简单编辑、格式化、分类 |
| Deepseek-v4-pro | 80.6% | **$3.48** | claude-cli | 标准实现、CRUD、样板代码、文档 |
| GPT-5.5 | 83.0% | $30.00 | codex | 测试生成、自动化任务、高覆盖率 |

### 路由表

| 任务类型 | 首选模型 | 备选模型 | 理由 |
|---------|---------|---------|------|
| 架构设计 / 系统设计 | claude-opus | claude-sonnet | 需要最强的全局推理能力 |
| 复杂调试（跨模块） | claude-opus | claude-sonnet | 根因分析需要深度理解 |
| 多文件重构 | claude-sonnet | deepseek-v4-pro | 质量和成本的平衡点 |
| 代码审查 | claude-sonnet | deepseek-v4-pro | 需要捕捉细微问题 |
| 标准实现（CRUD、功能） | deepseek-v4-pro | claude-sonnet | 80.6% SWE-bench，仅 1/4 成本 |
| 测试生成 | gpt-5.5 | deepseek-v4-pro | GPT-5.5 在测试覆盖率上表现突出 |
| 样板代码 / 脚手架 | deepseek-v4-pro | claude-haiku | 直接明了，成本敏感 |
| 文档编写 | deepseek-v4-pro | claude-haiku | 写作密集，无需复杂推理 |
| 简单编辑 / 配置修改 | claude-haiku | deepseek-v4-pro | 最快、最便宜 |

### 路由 CLI

```bash
# 查询推荐模型
PYTHONPATH=~/.aya/src python3 -m aya.workspace route-model implementation

# 查看所有已配置模型
PYTHONPATH=~/.aya/src python3 -m aya.workspace list-models
```

`route-model` 输出推荐模型、引擎和备选，PM spawn worker 前必须调用。如 PM 覆盖推荐，必须在事件日志中记录原因。

### 添加新模型

```bash
# 非交互式（适合脚本）
PYTHONPATH=~/.aya/src python3 -m aya.workspace setup deepseek-v4-pro \
  --base-url https://api.deepseek.com/v1 --api-key sk-xxx

# 交互式向导
PYTHONPATH=~/.aya/src python3 -m aya.workspace setup
```

配置存储在 `~/.aya/models.json`，引擎根据模型名自动判断：名字含 `gpt/o1/o3/o4` → codex，含 `claude/opus/sonnet/haiku` → claude-agent，其他 → claude-cli。

---

## 通信模式

AYA 支持两种 Worker 通信模式，PM 根据任务耦合程度选择。

### 决策树

```
并行任务 A 和任务 B 之间：
  ├── 文件完全不重叠，接口无耦合 → Sub-agent（独立模式）
  ├── 共享 read_files 但输出独立 → Sub-agent + Board 广播
  ├── 一个定义接口/类型，另一个消费 → Teammate（需要协商）
  ├── 双方共同定义 API/Schema/协议 → Teammate（必须）
  └── 运行时依赖（A 的输出是 B 的输入） → 串行（depends_on），非并行
```

### Sub-agent + Board 模式（独立任务）

Worker 作为一次性子 Agent 运行，完成后退出。通信是单向的：Worker 写 mailbox → PM 读取。PM 在 spawn 时将接口规范硬编码到 board/ 目录，Worker 只读取。

**适用场景：**
- "添加 README + LICENSE + CI 配置"——三个完全独立的文件
- "实现用户模块 + 商品模块 + E2E 测试"——各写各的目录
- "添加 4 个 CRUD 端点"——路由不冲突，模型层已存在

### Teammate 模式（耦合任务）

Worker 作为持久会话加入 Team，可以通过 `SendMessage` 实时互相通信，协商接口。PM 通过 `TeamCreate`/`TeamDelete` 管理团队生命周期。

**适用场景：**
- "实现认证中间件 + 实现需要认证的 API"——API Worker 需要知道中间件的函数签名
- "定义 protobuf Schema + 实现服务端 + 实现客户端"——三方必须就 Schema 达成一致
- "重构数据层 + 更新所有调用方"——接口变化需要同步

### 混合调度（同一项目两种模式并存）

```
Wave 1（并行）:
  ├── Teammate 组: auth-worker + api-worker（协商认证接口）
  └── Sub-agent: docs-worker（独立，读代码写文档）

Wave 2（Wave 1 完成后）:
  └── Sub-agent: test-worker（depends_on: auth + api，写测试）
```

PM 在同一条消息中发出 `TeamCreate` + `Agent(teammate)` + `Agent(sub-agent)`，实现真正的混合并行。

---

## 并行安全

每个 TaskSpec 声明两种文件列表：

- `owned_files`：Worker 独占写入，绝不与其他并行 Worker 共享
- `read_files`：只读引用，多个 Worker 可以同时读取

AYA 的三重保障：

1. **冲突预检测**：写完 TaskSpec 后立即运行 `check-file-conflicts`，如有 `owned_file` 重叠则阻塞，等待冲突 Worker 完成后再 spawn
2. **git worktree 隔离**：每个 Worker 在 `.aya-worktrees/<worker-id>/` 中独立运行，物理文件系统分离
3. **只 commit，不 merge**：Worker 只提交到自己的 `agent/<task-id>` 分支，PM 串行合并，无并发 merge 冲突

```
task-001: owned_files: [src/auth.py]     ← 可以并行运行
task-002: owned_files: [src/api.py]      ← 可以并行运行
task-003: owned_files: [src/auth.py]     ← 阻塞，直到 task-001 完成
```

---

## 架构

**关键设计**：协调层在仓库外，Worker worktree 在项目内，两者物理分离。

```
~/.aya/runtime/<project-hash>/     协调层（运行时，仓库外）
├── tasks/                         TaskSpec JSON（每个任务一文件）
├── mailbox/                       Agent 间消息传递
│   ├── pm-a3f2/                   PM 的收件箱（Workers 写这里）
│   └── pm-a3f2--worker-T1/        Worker 的收件箱（PM 写这里）
├── board/                         共享上下文（只读广播）
│   ├── requirements.md
│   ├── plan.md
│   └── interface-task-001.md      Worker 定义的接口规范
├── pms/                           PM 会话注册表
├── logs/                          Worker prompt 和结果日志
├── state.json                     项目状态
├── config.json                    模型注册表 + 路由规则
└── events.jsonl                   追加式审计日志

<project>/                         主仓库（PM 读 + merge）
├── .aya → symlink → runtime/      方便符号链接
└── .aya-worktrees/                Worker worktree（完成后清理）
    ├── worker-task-001/           独立 git worktree
    └── worker-task-002/
```

Worker prompt 包含两个绝对路径，职责完全分离：
- **工作目录** = worktree 路径（写代码的地方，不要 cd 出去）
- **通信目录** = runtime 路径（读任务 JSON、写 mailbox 消息的地方）

### PM 模式持久化（hooks.py）

`UserPromptSubmit` hook 在每次用户提交 prompt 时运行，检查当前项目是否有活跃 PM 会话：
- **每隔 3 条消息**注入一次 PM 模式提醒（sparse 版本，约 50 token）
- **每隔 5 次注入**发送一次完整提醒（full 版本，约 200 token，含完整工作流）
- 第一条消息始终发送完整提醒

这样即使 context 被压缩，模型仍会保持 PM 身份，无需手动重激活。

---

## CLI 参考

所有命令使用 `PYTHONPATH=~/.aya/src python3 -m aya.workspace` 前缀。

### 工作空间管理

```bash
# 初始化 + 注册 PM 会话
init --pm-session --task "任务描述"

# 查看所有 PM 会话
list-pms

# 查看当前状态（任务、worktree、费用）
status

# 获取 runtime 目录路径
runtime-dir
```

### 任务管理

```bash
# 写入任务
write-task '{"task_id":"task-001","title":"...","owned_files":[...],"engine":"claude-agent","model":"claude-opus"}'

# 更新任务状态
update-task task-001 '{"status":"done","result":"认证模块实现完成"}'

# 列出任务（可按 PM 过滤）
list-tasks [--pm pm-a3f2]

# 检查文件冲突
check-file-conflicts task-001
```

### Worker 管理

```bash
# 一步 spawn（自动建 worktree + 写 prompt + 生成命令）
cat <<'PROMPT' | spawn-worker task-001
...worker prompt...
PROMPT

# 手动 worktree 管理
create-worktree worker-task-001 agent/task-001
remove-worktree worker-task-001
cleanup-worktrees
```

### 模型与路由

```bash
# 查询路由推荐
route-model implementation   # 输出: model, engine, fallback

# 查看所有配置的模型（含可用性）
list-models

# 交互式模型配置向导
setup

# 非交互式添加模型
setup deepseek-v4-pro --base-url https://api.deepseek.com/v1 --api-key sk-xxx

# 查看简单模型列表
models

# 获取模型环境变量（供 claude -p 使用）
model-env deepseek-v4-pro
```

### 消息与日志

```bash
# 读取 PM 收件箱
read-inbox pm-a3f2

# 写入事件日志
log-event '{"actor":"pm","event_type":"worker.spawned","data":{"task_id":"task-001","model":"deepseek-v4-pro"}}'
```

### 环境与更新

```bash
# 检查引擎可用性
check-env

# 自更新（从 GitHub 拉取最新版）
self-update

# 查看版本
version
```

---

## 开发

```bash
# 运行测试
PYTHONPATH=src python3 -m pytest tests/ -v

# 开发模式安装（修改即生效，无需重装）
pip install -e ".[dev]"

# 测试 workspace CLI
PYTHONPATH=src python3 -m aya.workspace init --pm-session --task "test"
PYTHONPATH=src python3 -m aya.workspace status
```

项目结构：

```
src/aya/
├── __init__.py        版本号（git hooks 自动更新）
├── models.py          TaskSpec, Message, Event, PMSession dataclass
├── workspace.py       Workspace 类 + CLI 入口
└── hooks.py           UserPromptSubmit hook（PM 模式持久化）

.claude/skills/aya.md  PM 行为指令（skill 的权威来源）
tests/                 pytest 测试套件
install.sh             一键安装脚本
```

---

## 许可证

MIT
