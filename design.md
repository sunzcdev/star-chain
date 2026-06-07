# Phase 1 — 渠道适配层设计方案

> 基于 Phase 0 验证结果：OpenAI Agents SDK handoff 链路、SQLiteSession 持久化、三 Agent 流水线已跑通。
> Hermes weixin adapter 使用腾讯 iLink Bot API（长轮询 + 消息推送），可复用至本项目的 WeChatAdapter。

---

## 1. 整体架构

```
┌──────────────────────────────────────────────────┐
│                   用户（微信）                      │
└──────────────────┬───────────────────────────────┘
                   │ iLink Bot API (long-poll)
                   ▼
┌──────────────────────────────────────────────────┐
│  WeChatAdapter (ChannelAdapter 实现)              │
│  - _poll_loop: 长轮询 getupdates                   │
│  - send_message: 文本/媒体推送                      │
│  - context_token 管理                              │
└──────────────────┬───────────────────────────────┘
                   │ MessageEvent(user_id, text)
                   ▼
┌──────────────────────────────────────────────────┐
│  AgentRuntime                                     │
│  - 管理每个用户的 SQLiteSession                     │
│  - 持统一 model (DeepSeek/SiliconFlow)             │
│  - 调用 OpenAI Agents SDK Runner.run()             │
│  - Router Agent → Chat/Plan/Execute handoff 链     │
└──────────────────┬───────────────────────────────┘
                   │
      ┌────────────┴────────────┐
      ▼                         ▼
┌──────────────┐     ┌──────────────────────┐
│ SQLiteSession │     │ OpenAIChatCompletions│
│ (per user)    │     │ Model (DeepSeek/etc) │
└──────────────┘     └──────────────────────┘
```

**数据流：**
1. WeChatAdapter 长轮询拉取消息 → 识别 text → 包装为 MessageEvent
2. AgentRuntime.handle_message(user_id, text) 接收
3. Runtime 查找/创建该用户的 SQLiteSession
4. 调用 Runner.run() 传入消息 + session history
5. Router Agent 判断意图 → handoff 到 Chat/Plan/Execute
6. 最终响应通过 WeChatAdapter.send_message 回推给用户

---

## 2. ChannelAdapter 接口

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class MessageEvent:
    user_id: str       # 平台用户标识
    text: str          # 消息文本
    message_id: str    # 平台消息 ID


@dataclass
class SendResult:
    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None


class ChannelAdapter(ABC):
    """渠道适配器抽象接口——所有平台适配器实现此接口"""

    @abstractmethod
    async def start(self, on_message: Callable[[MessageEvent], None]) -> None:
        """启动监听，on_message 在收到消息时被回调"""
        pass

    @abstractmethod
    async def send_message(self, user_id: str, text: str) -> SendResult:
        """发送文本消息到用户"""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """停止监听并清理资源"""
        pass
```

**设计决策：** 保持接口极简。只需要「启动监听」「发送消息」「停止」。平台特有的认证、轮询策略、媒体处理都在实现类内部处理。

---

## 3. AgentRuntime 详细设计

```python
from openai import AsyncOpenAI
from agents import Agent, Runner, OpenAIChatCompletionsModel, AgentHooks
from agents import set_default_openai_key
from agents import RunConfig
from openai.types.chat import ChatCompletionMessageParam
from agents.run import RunResult, RunResultStreaming
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class AgentRuntime:
    """渠道适配层与 OpenAI Agents SDK 之间的桥梁"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str = "deepseek-chat",
        max_turns: int = 30,
        session_dir: str = "~/.agent-channel/sessions",
    ):
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self._model = OpenAIChatCompletionsModel(model=model, openai_client=self._client)
        self._max_turns = max_turns
        self._session_dir = Path(session_dir).expanduser()
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, "SessionContext"] = {}

        # 构建 Agent 流水线（Phase 0 已验证的架构）
        self._router = self._build_agents()

    def _build_agents(self) -> Agent:
        """构建三 Agent 流水线：Router → Chat/Plan/Execute"""
        execute_agent = Agent[None](
            name="Execute",
            instructions="你负责执行具体的代码/文件操作任务。只输出执行结果。",
            tools=[...],  # write_file, patch, terminal 等
            output_guardrails=[...],
        )
        plan_agent = Agent[None](
            name="Plan",
            instructions="你负责分析需求并制定实施方案。",
            tools=[...],  # read_file, search_files 等
            handoffs=[execute_agent],
        )
        chat_agent = Agent[None](
            name="Chat",
            instructions="你负责闲聊、解答问题。不调用文件写入工具。",
            input_guardrails=[...],
        )
        router = Agent[None](
            name="Router",
            instructions="判断用户意图，将对话 handoff 到合适的 Agent。",
            handoffs=[chat_agent, plan_agent],
        )
        return router

    async def handle_message(self, user_id: str, message: str) -> str:
        """处理用户消息，返回回复文本"""
        session = self._get_or_create_session(user_id)
        session.add_user_message(message)

        result = await Runner.run(
            self._router,
            input=session.history,
            run_config=RunConfig(
                model=self._model,
                max_turns=self._max_turns,
            ),
        )

        response = result.final_output
        session.add_assistant_message(response)
        session.save()
        return response

    async def new_session(self, user_id: str) -> None:
        """重置用户会话（/new 命令）"""
        if user_id in self._sessions:
            del self._sessions[user_id]
        # 删除本地 session 文件
        session_file = self._session_dir / f"{user_id}.json"
        if session_file.exists():
            session_file.unlink()

    def _get_or_create_session(self, user_id: str) -> "SessionContext":
        if user_id not in self._sessions:
            self._sessions[user_id] = SessionContext(
                user_id=user_id,
                storage_dir=self._session_dir,
            )
        return self._sessions[user_id]
```

### 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| Session 存储 | 每个用户一个 JSON 文件 | 简单、可调试、无需额外依赖 |
| 单用户 session_id | = weixin_user_id | 无需 mapping 表 |
| 模型 | DeepSeek (deepseek-chat) | 已验证，性价比高 |
| 流式响应 | 首次实现不做 streaming | iLink Bot API 不支持增量编辑，最终一次性推送即可 |
| 并发 | asyncio | 与 iLink adapter 的异步模型一致 |
| Agent 模板 | Phase 0 已验证的 Router→Chat/Plan/Execute | 直接复用 |

---

## 4. WeChatAdapter 实现方案

### 核心策略：复用 Hermes weixin adapter 的关键逻辑

Hermes 的 `gateway/platforms/weixin.py`（2171 行）已经实现了完整的 iLink Bot API 接入。我们的 WeChatAdapter 需要从中提取可复用部分。

### 4.1 复用 vs. 重写分析

| 模块 | 行数 | 复用方案 |
|------|------|---------|
| iLink API 通信层 (`_api_post`, `_api_get`, `_get_updates`, `_send_message`) | ~100 行 | **完整复制**——纯函数，无内部 Hermes 依赖 |
| 长轮询循环 (`_poll_loop`, `_process_message`) | ~100 行 | **改编**——回调方式改为调用 ChannelAdapter 的 on_message |
| 会话管理 (ContextTokenStore) | ~50 行 | **完整复制**——iLink 必须的 context_token 管理 |
| 媒体处理（下载/上传/加密） | ~300 行 | **跳过**——Phase 1 仅文本 |
| Markdown 格式化 (`_split_text_for_weixin_delivery` 等) | ~200 行 | **完整复制**——微信的 Markdown 渲染特殊处理 |
| 打字指示器 (send_typing/stop_typing) | ~50 行 | **复制**——提升用户体验 |
| 适配器框架 (`BasePlatformAdapter`) | ~2700 行 | **不依赖**——改用我们轻量的 ChannelAdapter 接口 |

### 4.2 WeChatAdapter 代码框架

```python
import asyncio
import aiohttp
import json
import logging
from typing import Optional, Callable

from .channel_adapter import ChannelAdapter, MessageEvent, SendResult

logger = logging.getLogger(__name__)

# iLink 常量
ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
LONG_POLL_TIMEOUT_MS = 35_000
API_TIMEOUT_MS = 15_000

# ==== 从 Hermes weixin.py 直接复用的部分 ====
def _json_dumps(payload: dict) -> str: ...
def _base_info() -> dict: ...
def _headers(token: str, body: str) -> dict: ...
async def _api_post(...) -> dict: ...
async def _get_updates(...) -> dict: ...
async def _send_message(...) -> dict: ...
# ============================================


class WeChatAdapter(ChannelAdapter):
    """微信适配器——通过 iLink Bot API 接入个人微信"""

    def __init__(self, token: str, account_id: str,
                 base_url: str = ILINK_BASE_URL):
        self._token = token
        self._account_id = account_id
        self._base_url = base_url
        self._session: Optional[aiohttp.ClientSession] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._running = False
        self._on_message: Optional[Callable] = None
        self._sync_buf = ""

    async def start(self, on_message: Callable[[MessageEvent], None]) -> None:
        self._on_message = on_message
        self._running = True
        self._session = aiohttp.ClientSession()
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def _poll_loop(self) -> None:
        """长轮询 iLink 拉取消息"""
        while self._running:
            try:
                response = await _get_updates(
                    self._session,
                    base_url=self._base_url,
                    token=self._token,
                    sync_buf=self._sync_buf,
                    timeout_ms=LONG_POLL_TIMEOUT_MS,
                )
                # 更新 sync_buf
                new_buf = response.get("get_updates_buf", "")
                if new_buf:
                    self._sync_buf = new_buf

                for msg in response.get("msgs", []):
                    text = self._extract_text(msg)
                    if text:
                        event = MessageEvent(
                            user_id=msg["from_user_id"],
                            text=text,
                            message_id=msg.get("message_id", ""),
                        )
                        if self._on_message:
                            await self._on_message(event)
            except asyncio.TimeoutError:
                continue  # 长轮询正常超时
            except Exception as e:
                logger.error("poll error: %s", e)
                await asyncio.sleep(2)

    async def send_message(self, user_id: str, text: str) -> SendResult:
        try:
            resp = await _send_message(
                self._session,
                base_url=self._base_url,
                token=self._token,
                to=user_id,
                text=text,
                context_token=None,  # Phase 1 暂不处理
                client_id=f"agent-channel-{user_id}",
            )
            return SendResult(success=True)
        except Exception as e:
            logger.error("send failed to %s: %s", user_id, e)
            return SendResult(success=False, error=str(e))

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
        if self._session:
            await self._session.close()

    def _extract_text(self, msg: dict) -> Optional[str]:
        """从 iLink 消息中提取文本内容"""
        for item in msg.get("item_list", []):
            if item.get("type") == 1:  # ITEM_TEXT
                return item.get("text_item", {}).get("text", "")
        return None
```

### 4.3 接入前需要准备

1. **iLink Bot Token** — 通过微信扫码注册 iLink 机器人获得
2. **account_id** — iLink 分配的机器人 ID
3. 安装依赖：`aiohttp`、`openai-agents`

---

## 5. Session 管理策略

### 5.1 会话模型

```
微信用户 A ─→ session_id = "wx_user_A_123"
  └→ SQLiteSession (或 JSON file)
      ├─ messages[] ← 对话历史
      ├─ system_prompt ← 系统提示词
      └─ metadata ← 用户偏好等

微信用户 B ─→ session_id = "wx_user_B_456"
  └→ SQLiteSession ...
```

### 5.2 Session 数据结构（JSON 方案）

```json
{
  "user_id": "wx_user_A_123",
  "created_at": "2026-06-07T02:00:00Z",
  "updated_at": "2026-06-07T02:30:00Z",
  "history": [
    {"role": "user", "content": "帮我写个 Python 脚本"},
    {"role": "assistant", "content": "好的，我来分析需求 ..."},
    {"role": "tool", "tool_call_id": "call_xxx", "content": "文件已保存"}
  ],
  "metadata": {
    "lang": "zh",
    "active_agent": "plan"
  }
}
```

### 5.3 Session 生命周期

| 事件 | 行为 |
|------|------|
| 用户首次发消息 | 创建新 session，初始化 history |
| 用户连续对话 | 追加消息到 history，达到上限时截断 |
| 用户发 `/new` | 清空 history，保留 session 文件 |
| 用户长时间不发言 | session 保留在磁盘，重新发言时加载 |
| 超时（24h） | 归档到 `.archived/` 目录 |

### 5.4 OpenAI Agents SDK 的 SQLiteSession

Phase 0 已经验证 `SQLiteSession` 可以零配置使用。如果后续需要更健壮的持久化，可以切到 SDK 原生方案：

```python
from agents import SQLiteSession, Session

session = SQLiteSession("~/.agent-channel/sessions.db")
```

但 Phase 1 建议先用文件 JSON，降低复杂度。

---

## 6. 启动和部署方案

### 6.1 单一入口

```python
# run.py — 项目入口
import asyncio
import logging

from runtime import AgentRuntime
from wechat_adapter import WeChatAdapter

logging.basicConfig(level=logging.INFO)

async def main():
    # 1. 初始化 AgentRuntime
    runtime = AgentRuntime(
        base_url="https://api.deepseek.com",
        api_key="sk-xxx",
        model="deepseek-chat",
    )

    # 2. 初始化 WeChatAdapter
    adapter = WeChatAdapter(
        token="your_ilink_token",
        account_id="your_account_id",
    )

    # 3. 启动监听
    async def on_message(event):
        response = await runtime.handle_message(event.user_id, event.text)
        await adapter.send_message(event.user_id, response)

    await adapter.start(on_message)

    # 4. 保持运行
    try:
        await asyncio.Future()  # 运行直到被中断
    finally:
        await adapter.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

### 6.2 进程管理

| 方案 | 适用场景 | 说明 |
|------|---------|------|
| `python run.py` | 开发测试 | 直接运行，CTRL+C 退出 |
| systemd service | 生产部署 | 自动重启、日志管理 |
| supervisor | 备选 | 配置简单，支持进程组 |

**Systemd 示例：**
```ini
[Unit]
Description=Agent Channel WeChat Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/projects/agent-channel
Environment=DEEPSEEK_API_KEY=sk-xxx
Environment=WEIXIN_TOKEN=xxx
ExecStart=/home/ubuntu/projects/agent-channel/.venv/bin/python run.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 6.3 环境变量配置

```
# API Keys
DEEPSEEK_API_KEY=sk-xxx

# WeChat iLink
WEIXIN_TOKEN=your_ilink_token
WEIXIN_ACCOUNT_ID=your_account_id
WEIXIN_BASE_URL=https://ilinkai.weixin.qq.com

# Runtime
AGENT_CHANNEL_SESSION_DIR=~/.agent-channel/sessions
AGENT_CHANNEL_LOG_LEVEL=INFO
```

---

## 7. 错误处理和日志策略

### 7.1 错误分级

| 级别 | 示例 | 处理方式 |
|------|------|---------|
| 可恢复 | iLink 网络超时、API 频率限制 | 自动重试（指数退避） |
| 需降级 | DeepSeek API 超时 | 返回兜底文案："暂时无法处理，请稍后再试" |
| 致命 | Token 过期、配置错误 | 记录日志，退出进程 |

### 7.2 日志策略

```
~/.agent-channel/logs/
├── agent-channel.log     # INFO 及以上，轮转 7 天
└── errors.log            # WARNING 及以上，长期保留
```

```python
import logging
from logging.handlers import TimedRotatingFileHandler

def setup_logging(log_dir: str = "~/.agent-channel/logs"):
    log_dir = Path(log_dir).expanduser()
    log_dir.mkdir(parents=True, exist_ok=True)

    # 主日志
    handler = TimedRotatingFileHandler(
        log_dir / "agent-channel.log",
        when="midnight", backupCount=7,
    )
    handler.setLevel(logging.INFO)
    logging.basicConfig(
        level=logging.INFO,
        handlers=[handler, logging.StreamHandler()],
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
```

### 7.3 超时策略

| 操作 | 超时 | 说明 |
|------|------|------|
| iLink 轮询 | 35s | 正常长轮询时间，超时重连 |
| iLink 发消息 | 15s | 单条消息发送 |
| DeepSeek API | 60s | Agent 单轮推理 |
| 总处理时间 | 120s | 一次用户请求的完成时间上限 |

---

## 8. 执行计划

### Step 1 — 创建项目骨架
- `mkdir -p ~/projects/agent-channel/`
- 初始化 Python 项目结构（`src/`, `tests/`, `run.py`, `pyproject.toml`）
- 安装依赖：`openai-agents`, `aiohttp`

### Step 2 — 实现 ChannelAdapter 接口
- 创建 `src/agent_channel/channel_adapter.py`
- 定义 `MessageEvent`, `SendResult`, `ChannelAdapter(ABC)`

### Step 3 — 实现 AgentRuntime
- 创建 `src/agent_channel/runtime.py`
- 从 Phase 0 的验证代码迁移 Agent 定义
- 集成 SessionContext（JSON 文件持久化）

### Step 4 — 实现 WeChatAdapter
- 创建 `src/agent_channel/wechat_adapter.py`
- 从 Hermes weixin.py 复制 iLink API 通信层
- 实现长轮询、消息提取、文本发送

### Step 5 — 组装入口 run.py
- 创建 `run.py`，组合 Runtime + Adapter
- 环境变量配置
- 日志初始化

### Step 6 — 测试和验证
- 用 iLink 测试号发消息验证端到端链路
- 测 `/new` 重置会话
- 测多用户并发

---

## 9. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| iLink Bot Token 不稳定（过期/被封） | 服务不可用 | 接入失败快速检测，日志报警 |
| DeepSeek API 延迟高 | 用户体验差 | 设置 60s 超时，降级到兜底文案 |
| 多用户并发导致内存膨胀 | OOM | 每个 session 限制 history 最大 50 条 |
| iLink 消息重复推送 | 用户收到重复回复 | 用 message_id 去重（Hermes 已有 dedup 逻辑） |
| Agent handoff 死循环 | 无限消耗 API | `max_turns=30` 硬限制 |
| iLink 长轮询断连 | 消息丢失 | 重连机制 + sync_buf 恢复上下文 |

---

## 10. 文件结构

```
~/projects/agent-channel/
├── run.py                          # 入口
├── pyproject.toml                  # 项目配置 + 依赖
├── README.md                       # 使用说明
├── src/
│   └── agent_channel/
│       ├── __init__.py
│       ├── channel_adapter.py      # 渠道适配器接口
│       ├── runtime.py              # AgentRuntime 实现
│       ├── wechat_adapter.py       # WeChatAdapter (iLink)
│       ├── session.py              # SessionContext 管理
│       └── utils.py                # 工具函数（日志等）
└── tests/
    ├── test_runtime.py
    └── test_wechat_adapter.py
```

---

## 附录：Phase 0 关键发现

| 发现 | 对 Phase 1 的影响 |
|------|------------------|
| Handoff 单向——控制权不自动返回 Router | AgentRuntime 需要确保 Router 作为唯一的入口/出口点 |
| SQLiteSession 内置支持 | 可以零配置使用，但 Phase 1 先用 JSON 文件降低复杂度 |
| Guardrails 分 input/output/tool 三级 | Router → Chat/Plan/Execute 流水线需要预设 guardrails |
| 原生 Tool API 完善 | 需要为 Plan Agent 配 read_file/search_files，Execute Agent 配 write_file/patch/terminal |
