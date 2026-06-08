---
date: 2026-06-07 周日
time: 17:30
status: 已归档
tags: [star-chain, architecture, adr, design]
---

# StarChain 架构设计文档

> 版本：v5.0 | 基准：[v4.0 — OpenAI Agents SDK + 渠道层方案]
> StarChain = OpenAI Agents SDK + 飞书渠道层 + 三段式 Agent (Chat/Plan/Executor)

---

## 一、设计目标

### 核心理念

**用 Agent 原生 handoff 机制做多 Agent 协作，再通过统一通道层接入 IM。**

市面上大多数 IM Bot 是「单 Agent + 工具路由」——一个 Agent 处理所有消息，通过工具切换模式。
StarChain 选择另一条路：**多个 Agent 通过 handoff 接力协作，每个 Agent 职责单一。**

### 设计原则

| 原则 | 说明 |
|------|------|
| **Agent = 角色** | 每个 Agent 是一个角色（Chat/Plan/Executor），有明确的职责边界 |
| **协作 = Handoff** | Agent 间通过 handoff 传递控制权，不是共享状态 |
| **通道 = 接口** | IM 平台通过 ChannelAdapter 接口接入，不影响 Agent 逻辑 |
| **工具 = 能力** | 工具层独立于 Agent 定义，Agent 按角色取用 |

### 与 Hermes 的关系

StarChain **不是 Hermes 的替代品**，是独立项目。

| | Hermes | StarChain |
|---|--------|-----------|
| Agent 协作 | delegate_task（手动） | Handoff（原生） |
| 通道层 | gateway/platforms/ | ChannelAdapter 接口 |
| 工具 | 60+ 内置 | Code · Web · Skill · MCP |
| 场景 | 全栈助手 | IM 协作 Agent |

---

## 二、架构

```
┌─────────────────────────────────────────────────────────┐
│                    飞书用户                                │
└────────────────────────┬────────────────────────────────┘
                         │ lark-oapi WebSocket
                         ▼
┌─────────────────────────────────────────────────────────┐
│  FeishuAdapter (ChannelAdapter 实现)                     │
│  - WebSocket 长连接 + 自动重连                            │
│  - Token 刷新 + 心跳                                     │
│  - 消息分发 + dedup                                      │
└────────────────────────┬────────────────────────────────┘
                         │ MessageEvent(user_id, text)
                         ▼
┌─────────────────────────────────────────────────────────┐
│  AgentRuntime                                            │
│  - Session 管理（per-user JSON, 50 条上限）               │
│  - Runner.run() 包装                                     │
│  - 超时处理（120s）                                       │
└───────────┬─────────────────────────────────────────────┘
            │
   ┌────────┼────────┐
   ▼        ▼        ▼
┌──────┐ ┌──────┐ ┌──────────┐
│ Chat  │ │ Plan  │ │ Executor │
│ 入口  │ │ 方案  │ │ 执行     │
└──┬───┘ └──┬───┘ └────┬─────┘
   │        │          │
   └────────┼──────────┘
            ▼
┌─────────────────────────────────────────────────────────┐
│                 工具层 (Tool Layer)                       │
│                                                          │
│  ┌──────────┐ ┌──────────┐ ┌─────────┐ ┌──────────┐   │
│  │ Code     │ │ Web      │ │ Skill   │ │ MCP      │   │
│  │ 原子能力 │ │ Search   │ │ Claude  │ │ Server   │   │
│  │ read/    │ │          │ │ Open    │ │ 调用     │   │
│  │ write/   │ │          │ │ Code    │ │          │   │
│  │ patch/   │ │          │ │         │ │          │   │
│  │ terminal │ │          │ │         │ │          │   │
│  └──────────┘ └──────────┘ └─────────┘ └──────────┘   │
└─────────────────────────────────────────────────────────┘
```

---

## 三、Agent 三级协作协议

### Chat Agent（入口/出口）

| 属性 | 值 |
|------|----|
| 角色 | 聊天助手 + 对话网关 |
| 入口 | 用户消息到达的第一站 |
| 出口 | 最终回复用户的最后一站 |
| 工具 | 无（只说话） |
| Handoff 目标 | Plan Agent（有需求时） |
| 接收 | Plan/Executor handoff 回来的结果 |

**职责：**
- 闲聊、概念讨论、探索想法
- 检测用户意图 → handoff 给 Plan
- 接收 Plan/Executor 的回复 → 转述给用户

### Plan Agent（方案规划）

| 属性 | 值 |
|------|----|
| 角色 | 方案规划师 |
| 工具 | 只读工具：`read_file`, `search_files`, `web_search` |
| Handoff 来源 | Chat Agent |
| Handoff 目标 | Executor Agent（方案就绪时），Chat Agent（需求不清晰时） |

**职责：**
- 分析用户需求
- 调研（读文件、搜网页）
- 输出可执行方案（步骤、依赖、预期结果）
- 方案确认 → handoff 给 Executor

### Executor Agent（执行）

| 属性 | 值 |
|------|----|
| 角色 | 执行者 |
| 工具 | 全工具：Code + Web + Skill + MCP |
| Handoff 来源 | Plan Agent |
| Handoff 目标 | Chat Agent（执行完毕） |

**职责：**
- 按方案执行
- 调用工具真实干活
- 报告结果

---

## 四、工具层

### Code 原子能力

| 工具 | 说明 | 来源 |
|------|------|------|
| `read_file` | 读文件 | OpenAI Agents SDK function_tool |
| `write_file` | 写文件 | OpenAI Agents SDK function_tool |
| `patch` | 编辑文件 | OpenAI Agents SDK function_tool |
| `search_files` | 搜索文件 | OpenAI Agents SDK function_tool |
| `terminal` | 执行命令 | OpenAI Agents SDK function_tool |
| `execute_code` | 运行 Python | OpenAI Agents SDK function_tool |

### Web Search

| 工具 | 说明 |
|------|------|
| `web_search` | 搜索网页 |
| `web_extract` | 提取页面内容 |

### Skill

| 工具 | 说明 |
|------|------|
| `call_claude_code` | 调用 Claude Code CLI |
| `call_open_code` | 调用 Open Code CLI |
| `run_skill` | 运行自定义 Skill 脚本 |

### MCP

| 工具 | 说明 |
|------|------|
| `mcp_call` | 调用 MCP Server 工具 |
| `mcp_list` | 列出可用 MCP 工具 |

---

## 五、通道层

### ChannelAdapter 接口

```python
class ChannelAdapter(ABC):
    async def start(self, on_message: Callable[[MessageEvent], None]) -> None
    async def send_message(self, user_id: str, text: str) -> SendResult
    async def stop(self) -> None
```

### FeishuAdapter（唯一实现）

基于 lark-oapi SDK 的 WebSocket 实现：
- 自动重连
- Token 刷新
- 群聊 @ 过滤
- chat_map 路由（open_id → chat_id）

---

## 六、数据流

```
用户: "帮我看看项目里有什么错误"
  → Chat Agent 接收
  → handoff_to_planner
  → Plan Agent:
      → read_file("项目文件")
      → web_search("常见错误模式")
      → 输出方案: "检查这三处"
      → handoff_to_executor
  → Executor Agent:
      → read_file("代码")
      → terminal("测试命令")
      → 找到错误，修好
      → handoff_to_chat
  → Chat Agent:
      → "找到3个错误，已修复。1)...2)...3)..."
```

---

## 七、项目结构

```
star-chain/
├── run.py                      # 入口（飞书）
├── pyproject.toml              # 项目配置
├── README.md                   # 项目说明
├── docs/
│   ├── architecture.md         # 架构设计（本文）
│   ├── adr/                    # 架构决策记录
│   └── obsidian/               # 从 Obsidian 迁移的笔记
├── src/
│   └── star_chain/
│       ├── __init__.py
│       ├── channel_adapter.py
│       ├── feishu_adapter.py
│       ├── feishu_login.py
│       ├── runtime.py
│       ├── agents/
│       │   └── __init__.py
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── code.py
│       │   ├── web.py
│       │   ├── skill.py
│       │   └── mcp.py
│       ├── session.py
│       ├── account_store.py
│       └── utils.py
├── tests/
└── scripts/
```

---

## 八、决策记录

| 决策 | 选择 | 理由 |
|------|------|------|
| Agent 框架 | OpenAI Agents SDK | handoff 原生、Guardrails 内建、社区活跃 |
| 模型 | OpenAI-compatible | 通过 `OpenAIChatCompletionsModel` 支持 DeepSeek/SiliconFlow |
| 通道 | 仅飞书 | Phase 1 精简，聚焦单一平台 |
| Agent 数量 | 3 (Chat/Plan/Executor) | 最小角色集合，可根据需要扩展 |
| 会话存储 | JSON 文件 | 简单、可调试、零依赖 |
| 工具层 | 独立模块 | 与 Agent 定义解耦，可独立扩展 |

---

## 九、演进路线

### Phase 2 — Agent 拓扑精炼 ✅（已完成）
- [x] 三 Agent 定义（Chat/Plan/Executor）
- [x] 给 Plan Agent 绑定只读工具
- [x] 给 Executor Agent 绑定全工具
- [x] 验证 handoff 链端到端

### Phase 3 — 工具层集成 ✅（代码已完成）
- [x] Code 原子能力实现（read_file, write_file, patch, search_files, terminal, execute_code）
- [x] Web Search 集成（search, extract — DuckDuckGo + httpx）
- [x] Skill 桥接（run_skill, call_claude_code, call_open_code）
- [x] MCP Client 接入（mcp_list, mcp_call — stdio client 管理）

### Phase 4 — 生产化（当前/待完成）
- [ ] 配置系统（YAML）- 可定制模型、工具、Agent 角色
- [ ] 自定义插件注册机制
- [ ] 流式响应（飞书消息逐段更新）
- [ ] 媒体消息支持（图片、文件）
- [ ] 部署方案（systemd / Docker）
- [ ] 监控和告警

---

## 参考

- [v4.0 — OpenAI Agents SDK + 渠道层方案] — StarChain 的原始方案
- [v3.0 — 复盘与多Agent协作方案思考] — 为什么选择这条路
- [v1.2 — 架构分岔路] — 最初的分岔决策
- OpenAI Agents SDK: https://github.com/openai/openai-agents-python
