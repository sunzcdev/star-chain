# StarChain

**多 Agent 协作通道层 —— 飞书 ↔ OpenAI Agents SDK ↔ 工具层**

## 核心理念

> 不是把 IM 接入 Agent，而是用 Agent 原生 handoff 机制做多 Agent 协作，再通过统一通道层接入 IM。

```
飞书用户 ──→ FeishuAdapter ──→ AgentRuntime ──→ LLM
                                    │
                          ┌─────────┼─────────┐
                          │         │         │
                       Chat      Plan     Executor
                          │         │         │
                          └─────────┼─────────┘
                                    │
                            ┌───────┴───────┐
                            │   工具层       │
                            │ Code · Web    │
                            │ Skill · MCP   │
                            └───────────────┘
```

## 架构概览

| 层 | 组件 | 职责 |
|----|------|------|
| **通道层** | FeishuAdapter | 飞书消息收发，基于 lark-oapi WebSocket |
| **Runtime** | AgentRuntime | Session 管理 + Runner.run() 包装 |
| **Agent 层** | Chat / Plan / Executor | 三段式 handoff 协作 |
| **工具层** | Code Tools + Web Search + Skills + MCP | 原子能力集合 |

## 快速开始

```bash
# 克隆
git clone git@github.com:sunzcdev/star-chain.git
cd star-chain

# 安装
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# 飞书绑定
python -m star_chain.feishu_login

# 启动
python run.py
```

## 项目状态

- [x] Phase 0 — 技术验证（OpenAI Agents SDK handoff 可行性）
- [x] Phase 1 — 飞书渠道层
- [x] Phase 2 — Agent 拓扑精炼（Chat/Plan/Executor）
- [x] Phase 3 — 工具层集成（Code · Web · Skill · MCP）
- [ ] Phase 4 — 生产化（部署 · 监控 · 流式响应 · 媒体消息）

## 许可证

MIT
