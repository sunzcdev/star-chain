"""Feishu (Lark) channel adapter — 飞书 Bot API WebSocket 集成。"""

import asyncio
import logging
from typing import Callable, Optional

from lark_oapi.channel import FeishuChannel
from lark_oapi.channel.types import InboundMessage

from .channel_adapter import ChannelAdapter, MessageEvent, SendResult

logger = logging.getLogger(__name__)


class FeishuAdapter(ChannelAdapter):
    """飞书适配器 — 通过 lark-oapi WebSocket 连接飞书 Bot。

    Usage::

        adapter = FeishuAdapter(app_id="...", app_secret="...")
        await adapter.start(on_message_callback)
        await adapter.stop()
    """

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
        self._chat_map: dict[str, str] = {}
        self._running = False

    async def start(
        self, on_message: Callable[[MessageEvent], None]
    ) -> None:
        self._on_message = on_message
        self._running = True
        self._channel = FeishuChannel(
            app_id=self._app_id,
            app_secret=self._app_secret,
        )
        self._channel.on("message", self._handle_message)
        self._channel.on("error", self._handle_error)
        self._channel.on("reconnecting", lambda: logger.info("Feishu WS reconnecting..."))
        self._channel.on("reconnected", lambda: logger.info("Feishu WS reconnected"))
        await self._channel.connect_until_ready(timeout=30)
        logger.info("FeishuAdapter started (app_id=%s)", self._app_id)

    async def send_message(self, user_id: str, text: str) -> SendResult:
        if not text or not text.strip():
            return SendResult(success=False, error="empty text")
        if not self._channel:
            return SendResult(success=False, error="Feishu channel not started")
        try:
            receive_id = self._chat_map.get(user_id) or user_id
            result = await self._channel.send(receive_id, {"text": text})
            if result.success:
                return SendResult(success=True, message_id=result.message_id)
            return SendResult(success=False, error=str(result.error) if result.error else "unknown error")
        except Exception as e:
            logger.error("send_message failed to %s: %s", user_id, e)
            return SendResult(success=False, error=str(e))

    async def stop(self) -> None:
        self._running = False
        if self._channel:
            await self._channel.disconnect()
            self._channel = None
        logger.info("FeishuAdapter stopped")

    async def _handle_message(self, msg: InboundMessage) -> None:
        if not self._on_message or not self._running:
            return
        sender_id = msg.sender.open_id if msg.sender else ""
        chat_id = msg.conversation.chat_id if msg.conversation else ""
        chat_type = msg.conversation.chat_type if msg.conversation else ""
        text = (msg.content_text or "").strip()
        message_id = msg.id
        if not text:
            return
        if chat_type == "group" and self._require_mention:
            if not msg.mentioned_bot:
                return
            text = _strip_bot_mention(text, msg.mentions)
        if chat_id and sender_id:
            self._chat_map[sender_id] = chat_id
        event = MessageEvent(user_id=sender_id, text=text, message_id=message_id)
        try:
            await self._on_message(event)
        except Exception as e:
            logger.error("on_message handler failed for %s: %s", sender_id, e)

    async def _handle_error(self, err: Exception) -> None:
        logger.error("FeishuChannel error: %s", err)


def _strip_bot_mention(text: str, mentions: list) -> str:
    for m in mentions or []:
        if m.is_bot and m.key and text.startswith(m.key):
            text = text[len(m.key):].strip()
            break
    return text
