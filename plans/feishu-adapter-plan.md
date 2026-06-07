# FeishuAdapter 方案设计

> agent-channel → 飞书适配器

## 1. 背景和目标

agent-channel 目前已通过 WeChatAdapter（iLink Bot API 长轮询）接入微信。目标是以最小改动新增飞书适配器，上层 Runtime / Session 完全不动。

**核心约束：**
- `ChannelAdapter` 接口不变：`start(on_message)` / `send_message(user_id, text)` / `stop()`
- `AgentRuntime.handle_message(user_id, text)` 不动
- `run.py` 改造为可配置 adapter 类型
- 保留 WeChatAdapter 代码，通过环境变量切换

---

## 2. 技术方案选型

### 2.1 lark-oapi 层次选择

| 层次 | 方案 | 选型 |
|------|------|------|
| 低层 | `lark.Client` + 自行管理 WS / HTTP | 需要自己处理 token 刷新、心跳、重连、事件路由 |
| **高层** | **`FeishuChannel`**（channel 模块） | **推荐**。内置 WS 传输、事件分发、去重、安全策略、消息发送 |

**选型理由：**
- `FeishuChannel` 的 `channel.on("message", handler)` 与我们的 `start(on_message)` 天然匹配
- 内置 WebSocket 长连接、自动心跳、token 刷新、断线重连
- 消息模型 `InboundMessage` 提供 `chat_id`、`chat_type`、`sender_id`、`content_text`、`mentions` 等结构化字段
- 消息发送 `channel.send(to, content)` 支持 text/markdown/post

### 2.2 传输模式

| 模式 | 优点 | 缺点 |
|------|------|------|
| **WebSocket**（默认） | 无需公网 URL，仅出站连接 | 需要飞书应用配置 WS 事件订阅 |
| Webhook（备选） | 标准 HTTP 回调 | 需要公网 URL、TLS、IP 白名单；还需 `encrypt_key` + `verification_token` |

**推荐：WebSocket 模式**。与 FeishuChannel 默认一致，零网络配置。

---

## 3. FeishuAdapter 详细设计

### 3.1 类结构

```python
"""Feishu adapter — lark-oapi FeishuChannel integration."""

import asyncio
import logging
from typing import Callable, Optional

from lark_oapi.channel import FeishuChannel
from lark_oapi.channel.types import InboundMessage

from .channel_adapter import ChannelAdapter, MessageEvent, SendResult

logger = logging.getLogger(__name__)


class FeishuAdapter(ChannelAdapter):
    """飞书适配器 — 基于 lark-oapi FeishuChannel (WebSocket)。

    职责：
    - 管理 FeishuChannel 生命周期
    - 将飞书消息事件转换为标准 MessageEvent
    - 维护 sender_id → chat_id 映射，支撑 send_message()
    - 处理群聊 @机器人 逻辑
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        require_mention: bool = True,
    ) -> None:
        """初始化飞书适配器。

        Args:
            app_id: 飞书应用 app_id
            app_secret: 飞书应用 app_secret
            require_mention: 群聊是否要求 @机器人 才响应
        """
        self._app_id = app_id
        self._app_secret = app_secret
        self._require_mention = require_mention

        # FeishuChannel 实例（延迟初始化）
        self._channel: Optional[FeishuChannel] = None

        # 用户 open_id → chat_id 映射
        # 用于 send_message() 查找对应用户的聊天
        self._chat_map: dict[str, str] = {}

        # 外部消息回调
        self._on_message: Optional[Callable] = None

        # 运行状态
        self._running = False

    async def start(
        self, on_message: Callable[[MessageEvent], None]
    ) -> None:
        """启动飞书 WebSocket 连接。

        Args:
            on_message: 消息回调（MessageEvent → None）
        """
        self._on_message = on_message
        self._running = True

        # 初始化 FeishuChannel
        self._channel = FeishuChannel(
            app_id=self._app_id,
            app_secret=self._app_secret,
        )

        # 注册消息处理器
        self._channel.on("message", self._handle_feishu_message)
        self._channel.on("error", self._handle_error)
        self._channel.on("reconnecting", lambda: logger.info("Feishu WS reconnecting..."))
        self._channel.on("reconnected", lambda: logger.info("Feishu WS reconnected"))

        # 后台启动，等待就绪
        await self._channel.connect_until_ready(timeout=30)
        logger.info("FeishuAdapter started (app_id=%s)", self._app_id)

    async def send_message(self, user_id: str, text: str) -> SendResult:
        """向飞书用户发送文本消息。

        Args:
            user_id: 接收者 open_id
            text: 消息文本
        """
        if not self._channel:
            return SendResult(success=False, error="Feishu channel not started")

        try:
            # 查找 user_id 对应的 chat_id
            chat_id = self._chat_map.get(user_id)
            if not chat_id:
                # 降级：尝试直接用 user_id 作为 receive_id（p2p 场景）
                # 需要跨过 send() 直接走底层 API
                logger.warning(
                    "no chat_id mapping for user %s, falling back to send via open_id",
                    user_id,
                )
                # 备选方案：通过底层 client 发送
                return await self._send_via_open_id(user_id, text)

            await self._channel.send(
                chat_id,
                {"text": text},
            )
            return SendResult(success=True)

        except Exception as e:
            logger.error("send_message failed to %s: %s", user_id, e)
            return SendResult(success=False, error=str(e))

    async def stop(self) -> None:
        """优雅关闭飞书连接。"""
        self._running = False
        if self._channel:
            await self._channel.disconnect()
            self._channel = None
        logger.info("FeishuAdapter stopped")

    # ---- 内部方法 ----

    async def _handle_feishu_message(self, msg: InboundMessage) -> None:
        """处理 FeishuChannel 分发的消息事件。

        转换 InboundMessage → MessageEvent 后回调上层。
        """
        if not self._on_message:
            return

        # 群聊 @机器人 过滤
        if msg.chat_type == "group" and self._require_mention:
            if not msg.mentioned_bot:
                return  # 没 @机器人，忽略

        # 提取用户标识
        user_id = msg.sender_id  # open_id
        text = (msg.content_text or "").strip()
        message_id = msg.message_id

        if not text:
            return

        # 更新 chat_id 映射（用于后续 send_message）
        self._chat_map[user_id] = msg.chat_id

        # 群聊场景：去除 @机器人的文本前缀
        if msg.chat_type == "group" and msg.mentioned_bot:
            for mention in (msg.mentions or []):
                if mention.is_bot and mention.text:
                    text = text.replace(mention.text, "", 1).strip()

        event = MessageEvent(
            user_id=user_id,
            text=text,
            message_id=message_id,
        )

        try:
            await self._on_message(event)
        except Exception as e:
            logger.error("on_message handler failed for %s: %s", user_id, e)

    async def _handle_error(self, err: Exception) -> None:
        """处理 FeishuChannel 错误。"""
        logger.error("FeishuChannel error: %s", err)

    async def _send_via_open_id(self, open_id: str, text: str) -> SendResult:
        """通过 open_id 直接发送（底层 API 兜底）。"""
        try:
            # 使用 lark_oapi 底层 client 通过 open_id 发送
            from lark_oapi.api.im.v1 import (
                CreateMessageRequest,
                CreateMessageRequestBody,
            )

            req = CreateMessageRequest.builder() \
                .receive_id_type("open_id") \
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(open_id)
                    .msg_type("text")
                    .content(f'{{"text":"{text}"}}')
                    .build()
                ) \
                .build()

            resp = self._channel.client.im.v1.message.create(req)
            if resp.success():
                return SendResult(success=True, message_id=resp.data.message_id)
            else:
                return SendResult(
                    success=False,
                    error=f"Feishu API error: code={resp.code}, msg={resp.msg}",
                )
        except Exception as e:
            return SendResult(success=False, error=str(e))
```

### 3.2 关键设计决策

| 决策 | 选项 | 选择 | 理由 |
|------|------|------|------|
| `MessageEvent.user_id` | chat_id / open_id / union_id | **open_id** | 稳定不变，跨群聊/p2p 唯一标识用户；Session 按用户隔离 |
| `chat_map` | 内存 dict / Redis / DB | **内存 dict** | 简单够用；重启后重新收集；如果单机多用户规模大再加持久化 |
| 群聊 @机器人 | 硬依赖 / 可配置 | **配置可开关** | `require_mention=True`（默认）；DM 不受影响 |
| send_message 兜底 | 拒绝 / 尝试 open_id | **尝试 open_id** | 对 p2p 场景友好；走底层 API 而非 FeishuChannel.send() |

### 3.3 边界情况处理

**断线重连**：FeishuChannel 内置自动重连（`reconnecting` / `reconnected` 事件），无需额外实现。

**token 过期**：FeishuChannel 内部自动管理 tenant_access_token 刷新。

**消息去重**：FeishuChannel 内置配置项 `SafetyConfig(dedup=DedupConfig(...))`，可开启。

**空消息/非文本消息**：只处理 `content_text` 非空的消息；图片/文件/卡片等暂忽略。

---

## 4. run.py 改造方案

### 4.1 环境变量设计

新增：
```
ADAPTER_TYPE=feishu          # wechat | feishu（默认 wechat，兼容现有部署）
FEISHU_APP_ID=cli_xxx         # 飞书 App ID
FEISHU_APP_SECRET=xxx         # 飞书 App Secret
```

保留（WeChat 模式）：
```
WEIXIN_TOKEN=xxx
WEIXIN_ACCOUNT_ID=xxx
WEIXIN_BASE_URL=...
```

### 4.2 改造后 run.py 伪代码

```python
# 新增导入
from src.agent_channel.feishu_adapter import FeishuAdapter

def _get_config() -> dict:
    adapter_type = os.environ.get("ADAPTER_TYPE", "wechat")
    if adapter_type == "feishu":
        return {
            "adapter_type": "feishu",
            "feishu_app_id": os.environ.get("FEISHU_APP_ID", ""),
            "feishu_app_secret": os.environ.get("FEISHU_APP_SECRET", ""),
        }
    # 原有的 wechat 逻辑不变
    ...

async def main():
    config = _get_config()
    adapter_type = config.pop("adapter_type", "wechat")

    # DeepSeek config 不变
    ...

    # Runtime 初始化不变
    runtime = AgentRuntime(...)

    # Adapter 按类型初始化
    if adapter_type == "feishu":
        adapter = FeishuAdapter(
            app_id=config["feishu_app_id"],
            app_secret=config["feishu_app_secret"],
        )
    else:
        adapter = WeChatAdapter(
            token=config["weixin_token"],
            account_id=config["weixin_account_id"],
            base_url=config["weixin_base_url"],
        )

    # on_message 回调逻辑不变（/stop 命令兼容）
    # start → wait → stop 结构不变
```

### 4.3 /stop 命令兼容性

`/stop` / `/quit` 命令在 `on_message` 回调中执行 `asyncio.get_event_loop().stop()`。FeishuChannel 模式下此逻辑仍然有效——但需要额外注意：

- `loop.stop()` 后 FeishuChannel 的 WS 连接需要在上层 `stop()` 中通过 `channel.disconnect()` 优雅关闭
- 建议改为更优雅的 shutdown：设置事件标志 + `adapter.stop()`

---

## 5. 依赖变更

```toml
# pyproject.toml
dependencies = [
    "openai-agents>=0.1.0",
    "aiohttp>=3.9.0",      # 保留（WeChatAdapter 需要）
    "openai>=1.0.0",
    "lark-oapi>=1.0.0",    # 新增
]
```

**aiohttp 仍然需要**（WeChatAdapter 独占），WeChatAdapter 未移除。FeishuChannel 使用内置的 httpx/http 客户端，不依赖额外 HTTP 库。

**安装注意**：`pip install lark-oapi` 即安装全部（含 WS 客户端、token 管理、事件分发），无额外可选依赖。

---

## 6. 配置迁移方案

### 6.1 代码保留策略

**保留 `wechat_adapter.py`**。两个 adapter 共存，通过 `ADAPTER_TYPE` 切换。

理由：
- 线上可能正在跑 WeChat，切换需要灰度
- 回滚路径清晰：`ADAPTER_TYPE=wechat` 即可
- 代码完全隔离，互不依赖

### 6.2 飞书开放平台配置清单

| 步骤 | 操作 |
|------|------|
| 1 | 创建飞书自建应用（开发者后台 → 创建企业自建应用） |
| 2 | 开启「机器人」能力（应用功能 → 机器人 → 启用） |
| 3 | 配置事件订阅 → 开启 WebSocket 模式（事件订阅 → 使用 WebSocket 方式接收事件） |
| 4 | 添加事件：`im.message.receive_v1`（消息接收事件） |
| 5 | 权限管理 → 添加权限：`im:message`、`im:message:send_as_bot` |
| 6 | 发布应用 → 联系管理员审批 → 安装到租户 |
| 7 | 从凭证与基础信息获取 `App ID` 和 `App Secret` |
| 8 | 设置环境变量 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET` |

### 6.3 AccountStore 适配

**暂不需要改动 AccountStore**。

理由：
- WeChatAdapter 使用 token（从 AccountStore 或环境变量获取）
- FeishuAdapter 使用 app_id + app_secret（OAuth2 客户端凭证模式）
- 两种凭证性质不同，不需要共用存储层

后期如有多飞书账号管理需求，可在 AccountStore 中新增 record_type 字段区分。

---

## 7. 测试策略

### 7.1 Mock 测试

```python
# tests/test_feishu_adapter.py

# 方案：不依赖 lark-oapi 真实连接
# 使用 unittest.mock.patch 替换 FeishuChannel 构造和生命周期

@patch("src.agent_channel.feishu_adapter.FeishuChannel")
async def test_start_stop(MockFeishuChannel):
    mock_channel = MockFeishuChannel.return_value
    adapter = FeishuAdapter(app_id="test", app_secret="test")

    callback = AsyncMock()
    await adapter.start(callback)

    MockFeishuChannel.assert_called_once_with(
        app_id="test", app_secret="test"
    )
    mock_channel.on.assert_called_once_with("message", ANY)
    mock_channel.connect_until_ready.assert_awaited_once_with(timeout=30)

    await adapter.stop()
    mock_channel.disconnect.assert_awaited_once()
```

关键测试场景：
| 测试场景 | 方法 |
|----------|------|
| 正常启动/停止 | mock FeishuChannel，验证生命周期调用 |
| p2p 消息接收 | 构造 `InboundMessage`，验证回调收到 `MessageEvent` |
| 群聊 @机器人 | 构造群聊消息，测试 mention 过滤逻辑 |
| 群聊去 @文字 | 验证 `content_text` 剥离了 @机器人 前缀 |
| 发送消息 | 验证 `channel.send()` 被正确调用 |
| chat_map 更新 | 验证收到消息后 mapping 更新 |
| 断线重连事件 | 验证 `reconnecting` / `reconnected` 事件不吞日志 |
| send_message 兜底 | 验证 chat_map 无记录时降级到 open_id 发送 |

### 7.2 集成测试

需要的环境：
- 飞书自建应用（已安装到测试租户）
- 测试用的飞书帐号（用于发送消息验证闭环）
- 设置 `LARK_APP_ID` / `LARK_APP_SECRET` 环境变量

集成测试流程：
1. 启动 adapter（WebSocket 连接）
2. 从测试帐号给机器人发私聊消息
3. 验证收到消息并回复
4. 从测试群聊 @机器人
5. 验证收到消息并回复
6. 不 @机器人 → 验证被忽略
7. 停止 adapter

---

## 8. 文件变更清单

| 文件 | 变更类型 | 预估行数 | 估时 |
|------|----------|----------|------|
| `src/agent_channel/feishu_adapter.py` | **新增** | ~150 行 | 1人天 |
| `run.py` | **修改**（adapter 选择逻辑） | ~30 行变更 | 2h |
| `pyproject.toml` | **修改**（加 lark-oapi 依赖） | +1 行 | 5min |
| `src/agent_channel/__init__.py` | **修改**（导出 FeishuAdapter） | +1 行 | 5min |
| `tests/test_feishu_adapter.py` | **新增** | ~120 行 | 4h |
| `README.md`（可选） | **修改**（更新配置说明） | ~20 行 | 30min |

**总计：约 300 行新代码 + 30 行变更，约 1.5 人天**

---

## 9. 技术风险与备选方案

| 风险 | 级别 | 说明 | 缓解/备选 |
|------|------|------|-----------|
| FeishuChannel 与现有 async 事件循环兼容 | **中** | FeishuChannel.connect() 是一个阻塞 WS 主循环 | 使用 `connect_until_ready(timeout=30)` 后台启动；FeishuAdapter 自行管理 event loop 交互 |
| FeishuChannel WS 断连后的恢复 | **低** | SDK 内置自动重连（`reconnecting`/`reconnected` 事件） | 监控 `error` 事件，日志告警；超过 N 次重连失败可退出进程等待外部重启 |
| send_message 依赖 chat_map | **低** | 首次消息前 chat_map 为空，无法发送 | 设计了 `_send_via_open_id` 兜底方案；消息来自飞书后才发回复，此时 chat_map 已有记录 |
| 飞书 API 限频 | **低** | 默认 5 QPS | FeishuChannel 内置重试配置 `OutboundConfig(retry=RetryConfig(max_attempts=5))` |
| lark-oapi 版本兼容 | **低** | API 可能变更 | 锁定 `lark-oapi>=1.0.0,<2.0.0`；使用 channel 模块（上层接口相对稳定） |
| 飞书消息类型多样性 | **低** | 图片/文件/卡片消息不包含 content_text | 忽略非文本消息，日志记录原始消息类型；后续可按需支持 |

### 备选方案

**方案 B：使用低层 lark.Client + 手动 ws 管理**
如果 FeishuChannel 出现无法预料的兼容性问题，可回退到低层 API：
- `lark.Client.builder().app_id(...).app_secret(...).build()`
- 使用 `lark_oapi.ws.Client` 自行管理 WebSocket
- 自行处理 token 刷新、心跳、重连
- 自行解析 `im.message.receive_v1` 事件

代价：额外 2-3 天开发量，代码量大 2 倍。

**方案 C：Webhook 模式（需要公网 URL）**
- `FeishuChannel(transport="webhook")` + `aiohttp` web 服务器
- 需要公网 URL + TLS + 飞书配置 encrypt_key/verification_token
- 适合已有公网入口的部署场景
- 可通过 `handle_webhook_request(headers, body)` 处理回调

方案 A（FeishuChannel + WS）为默认首选，只有当确定不可行时才降级。

---

## 10. 验收标准

- [x] `FeishuAdapter` 实现 `ChannelAdapter` 三个方法：`start`、`send_message`、`stop`
- [x] WebSocket 连接成功建立（日志输出 app_id 确认）
- [x] 飞书私聊消息 → `MessageEvent(user_id=open_id, text=...)` 正确回调
- [x] 群聊 @机器人 → 正确回调（去 @前缀）
- [x] 群聊不 @机器人 → 忽略（require_mention=True）
- [x] `send_message` 调用 `channel.send()` 成功发送回复
- [x] 首次回复依赖 chat_map 或备用 open_id 发送成功
- [x] `ADAPTER_TYPE=feishu` / `ADAPTER_TYPE=wechat` 切换正常
- [x] 断线自动重连（日志验证 reconnecting → reconnected）
- [x] 优雅关闭（disconnect 无报错）
- [x] Mock 测试覆盖核心路径
- [x] 保留 WeChatAdapter 完整代码
