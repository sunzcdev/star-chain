# FeishuAdapter 实现 — 可执行待办列表

> 方案文档：`plans/feishu-adapter-plan.md`
> 基线项目：`~/projects/agent-channel/`
> 工作模式：Executor 在 `dir` 工作区直接操作

---

## 依赖关系图

```
T-1 (脚手架)
  └─→ T-2 (核心适配器)
        ├─→ T-3 (运行入口切换)
        └─→ T-4 (Mock 测试)
              └─→ T-5 (文档更新)
```

T-3 和 T-4 可并行（均依赖 T-2，互不依赖）。

---

## T-1：依赖与脚手架

**目的**：安装 lark-oapi SDK，创建 FeishuAdapter 文件骨架，更新包导出。

| 维度 | 内容 |
|------|------|
| 涉及文件 | `pyproject.toml`, `src/agent_channel/feishu_adapter.py`（新建）, `src/agent_channel/__init__.py` |
| 前置依赖 | 无 |
| 预估 | ~50 行变更 / 8-12 轮 |

**变更详情**：

1. **pyproject.toml** — 添加 `"lark-oapi>=1.0.0,<2.0.0"` 到 dependencies
2. **feishu_adapter.py** — 创建文件，仅含：
   - 模块 docstring
   - `from lark_oapi.channel import FeishuChannel`
   - `from lark_oapi.channel.types import InboundMessage`
   - `class FeishuAdapter(ChannelAdapter):` 空骨架（pass 方法体）
   - 必要的 import（logging, typing, etc.）
3. **__init__.py** — 添加 `from .feishu_adapter import FeishuAdapter`，加入 `__all__`

**验收标准**：
- [ ] `pip install -e .` 成功安装 lark-oapi
- [ ] `python -c "from src.agent_channel.feishu_adapter import FeishuAdapter"` 无报错
- [ ] `python -c "from src.agent_channel import FeishuAdapter"` 无报错

---

## T-2：FeishuAdapter 核心类

**目的**：实现完整的 FeishuAdapter 类，包含 WebSocket 监听、消息处理、chat_map 映射、send_message 兜底。

| 维度 | 内容 |
|------|------|
| 涉及文件 | `src/agent_channel/feishu_adapter.py` |
| 前置依赖 | T-1 |
| 预估 | ~200 行 / 15-20 轮 |

**设计要点**（从方案文档提取）：

### 构造函数
```python
class FeishuAdapter(ChannelAdapter):
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        require_mention: bool = True,
    ) -> None
```

### start(on_message)
- 创建 FeishuChannel 实例，传入 app_id/app_secret
- 注册事件处理器（`im.message.receive_v1`）
- 在事件回调中解析 InboundMessage：
  - 提取 sender（open_id）、text、chat_id
  - 群聊消息检查 `require_mention`（有 @机器人才响应）
  - 自动去掉消息中的 @前缀
  - 维护 `_chat_map[sender.open_id] = chat_id`
  - 构造 `MessageEvent` 并调用 on_message
- 调用 `FeishuChannel.start()` 启动 WebSocket 连接

### send_message(user_id, text)
- 优先从 `_chat_map` 查询 chat_id，使用 FeishuChannel.send_text(chat_id, text)
- 兜底：使用 `lark_oapi.api.im.v1.create_message` 通过 open_id 发送

### stop()
- 调用 `FeishuChannel.stop()`
- 清理资源

### 关键代码模式
```python
import asyncio
import logging

from lark_oapi.channel import FeishuChannel
from lark_oapi.channel.types import InboundMessage

from .channel_adapter import ChannelAdapter, MessageEvent, SendResult

logger = logging.getLogger(__name__)


class FeishuAdapter(ChannelAdapter):
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        require_mention: bool = True,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._require_mention = require_mention
        self._channel: Optional[FeishuChannel] = None
        self._on_message: Optional[Callable] = None
        self._chat_map: dict[str, str] = {}  # open_id → chat_id
        self._running = False

    async def start(self, on_message: Callable[[MessageEvent], None]) -> None:
        self._on_message = on_message
        self._running = True

        self._channel = FeishuChannel(
            app_id=self._app_id,
            app_secret=self._app_secret,
        )

        @self._channel.on("im.message.receive_v1")
        async def handle_message(msg: InboundMessage) -> None:
            # parse sender info
            sender = msg.sender
            if not sender or not sender.id:
                return

            open_id = sender.id.open_id
            chat_id = msg.message_id.chat_id  # or similar path
            text = self._extract_text(msg)

            if not text:
                return

            # group chat @check
            if msg.chat_type == "group" and self._require_mention:
                if not self._is_mentioned(msg):
                    return
                text = self._strip_mention(text)

            # update chat_map
            if chat_id:
                self._chat_map[open_id] = chat_id

            event = MessageEvent(
                user_id=open_id,
                text=text,
                message_id=msg.message_id.message_id,
            )

            if self._on_message:
                try:
                    await self._on_message(event)
                except Exception as e:
                    logger.error("on_message handler failed: %s", e)

        await self._channel.start()

    async def send_message(self, user_id: str, text: str) -> SendResult:
        if not text or not text.strip():
            return SendResult(success=False, error="empty text")

        try:
            chat_id = self._chat_map.get(user_id)
            if chat_id:
                # send via channel
                await self._channel.send_text(chat_id, text)
                return SendResult(success=True)

            # fallback: send via open_id using low-level API
            return await self._send_by_open_id(user_id, text)
        except Exception as e:
            logger.error("send_message failed: %s", e)
            return SendResult(success=False, error=str(e))

    async def stop(self) -> None:
        self._running = False
        if self._channel:
            await self._channel.stop()
        ...
```

### 注意事项
- `lark_oapi.channel` 是高层封装，需确认具体 API 命名（FeishuChannel 构造参数、事件注册方式、send_text 方法签名）。如果实际 API 不同，以 SDK 为准
- 需要处理好 `InboundMessage` 的结构解析（不同 SDK 版本的 `msg.message_id` 层级可能不同）
- chat_map 仅内存持有，重启后丢失。群聊用户首次发送私聊后会重新加入映射

### 验收标准
- [ ] `FeishuAdapter` 可以无报错实例化
- [ ] `start()` 调用后 `_running` 为 True，channel 已创建
- [ ] `stop()` 正确关闭 channel 并重置状态
- [ ] send_message 在有 chat_map 的情况下走 channel.send_text 路径
- [ ] send_message 空文本返回错误

---

## T-3：运行入口 ADAPTER_TYPE 切换

**目的**：修改 run.py 支持 `ADAPTER_TYPE=feishu` 环境变量切换适配器。

| 维度 | 内容 |
|------|------|
| 涉及文件 | `run.py` |
| 前置依赖 | T-2 |
| 预估 | ~40 行变更 / 5-8 轮 |

**变更详情**：

1. 在 run.py 顶部 import FeishuAdapter：`from src.agent_channel.feishu_adapter import FeishuAdapter`
2. 在 `_get_config()` 中增加适配器类型判断：
   - 读取 `ADAPTER_TYPE` 环境变量（默认 `wechat`）
   - 新增 `_get_feishu_config()` 读取飞书配置（`FEISHU_APP_ID`, `FEISHU_APP_SECRET`）
3. 在 main() 中根据 ADAPTER_TYPE 选择创建 WeChatAdapter 或 FeishuAdapter
4. 保持现有 WeChatAdapter 代码完整不变

**风险**：FeishuAdapter 暂未实测 WebSocket 连接，run.py 层面的集成预期会有调整

### 验收标准
- [ ] `ADAPTER_TYPE=wechat` 时行为与变更前完全一致（回归）
- [ ] `ADAPTER_TYPE=feishu` 时创建 FeishuAdapter 实例
- [ ] `ADAPTER_TYPE` 不设置时默认走 wechat 路径（向后兼容）
- [ ] 所有现有测试通过

---

## T-4：Mock 测试

**目的**：编写 FeishuAdapter 的 Mock 测试，覆盖消息处理、send_message、start/stop 生命周期。

| 维度 | 内容 |
|------|------|
| 涉及文件 | `tests/test_feishu_adapter.py`（新建） |
| 前置依赖 | T-2 |
| 预估 | ~120 行 / 10-15 轮 |

**测试场景**：

1. **test_feishu_adapter_import** — 验证类可实例化
2. **test_feishu_adapter_process_message** — Mock FeishuChannel，模拟收到 InboundMessage，验证 MessageEvent 正确构造
3. **test_feishu_adapter_process_group_message** — Mock 群聊消息（含 @机器人），验证 require_mention 过滤和去 @前缀
4. **test_feishu_adapter_send_message** — Mock FeishuChannel.send_text，验证调用参数
5. **test_feishu_adapter_send_message_empty** — 空文本返回 error
6. **test_feishu_adapter_stop** — 验证 stop 调用 channel.stop
7. **test_feishu_adapter_chat_map** — 验证接收消息后 chat_map 更新，send_message 优先走 chat_id

**测试策略**：
- 使用 unittest.mock 或 pytest monkeypatch Mock FeishuChannel
- 不依赖真实 lark-oapi 连接
- 参考 `tests/test_integration.py` 中的 Mock 模式

### 验收标准
- [ ] `python -m pytest tests/test_feishu_adapter.py -v` 全部测试通过
- [ ] `python -m pytest tests/ -v` 全部（新旧）测试通过
- [ ] 代码覆盖 FeishuAdapter 主要公共方法

---

## T-5：文档更新

**目的**：更新 README.md 增加飞书适配器的配置说明。

| 维度 | 内容 |
|------|------|
| 涉及文件 | `README.md` |
| 前置依赖 | T-3 |
| 预估 | ~20 行 / 3-5 轮 |

**变更详情**：

1. 在"架构"部分增加飞书适配器路径：
   ```
   用户（飞书） ──→ FeishuAdapter ──→ AgentRuntime ──→ AI Model
                     │
               lark-oapi WebSocket
   ```

2. 在"环境变量参考"表新增：
   | ADAPTER_TYPE | wechat | 适配器类型（wechat/feishu） |
   | FEISHU_APP_ID | — | 飞书开放平台 App ID |
   | FEISHU_APP_SECRET | — | 飞书开放平台 App Secret |

### 验收标准
- [ ] README.md 包含飞书配置说明
- [ ] 环境变量表完整覆盖 ADAPTER_TYPE、FEISHU_APP_ID、FEISHU_APP_SECRET

---

## 汇总

| 任务 | 涉及文件 | 预估行数 | 预估轮次 | 前置 |
|------|----------|----------|----------|------|
| T-1 | pyproject.toml, feishu_adapter.py, __init__.py | ~50 | 8-12 | — |
| T-2 | feishu_adapter.py | ~200 | 15-20 | T-1 |
| T-3 | run.py | ~40 | 5-8 | T-2 |
| T-4 | test_feishu_adapter.py | ~120 | 10-15 | T-2 |
| T-5 | README.md | ~20 | 3-5 | T-3 |
| **合计** | **5 文件** | **~430** | **~55** | |

> 注：T-3 和 T-4 可并行执行，实际耗时约 T-2 + max(T-3, T-4) + T-5 = ~40 轮。
