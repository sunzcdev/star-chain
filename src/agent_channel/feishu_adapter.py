"""Feishu (Lark) channel adapter — integration with Feishu Bot API via lark-oapi SDK.

Uses the high-level FeishuChannel API (WebSocket transport) with built-in:
- WebSocket long connection with auto-reconnect
- Token refresh and heartbeat
- Event dispatching and dedup
"""

import asyncio
import logging
from typing import Callable, Optional

from lark_oapi.channel import FeishuChannel
from lark_oapi.channel.types import InboundMessage

from .channel_adapter import ChannelAdapter, MessageEvent, SendResult

logger = logging.getLogger(__name__)


class FeishuAdapter(ChannelAdapter):
    """Feishu channel adapter — connects to Feishu Bot via lark-oapi WebSocket.

    Usage::

        adapter = FeishuAdapter(app_id="...", app_secret="...")
        await adapter.start(on_message_callback)
        # ... run ...
        await adapter.stop()
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        require_mention: bool = True,
    ) -> None:
        """Initialize the Feishu adapter.

        Args:
            app_id: Feishu app ID from the developer console.
            app_secret: Feishu app secret from the developer console.
            require_mention: In group chats, only respond when the bot is
                @-mentioned. Direct messages are always processed.
        """
        self._app_id = app_id
        self._app_secret = app_secret
        self._require_mention = require_mention

        self._channel: Optional[FeishuChannel] = None
        self._on_message: Optional[Callable] = None
        # open_id -> chat_id mapping, populated on each incoming message
        # so send_message() can route replies to the right conversation.
        self._chat_map: dict[str, str] = {}
        self._running = False

    async def start(
        self, on_message: Callable[[MessageEvent], None]
    ) -> None:
        """Start WebSocket connection and begin listening for messages.

        Args:
            on_message: Async callback invoked on each incoming MessageEvent.
        """
        self._on_message = on_message
        self._running = True

        self._channel = FeishuChannel(
            app_id=self._app_id,
            app_secret=self._app_secret,
        )

        # Register event handlers
        self._channel.on("message", self._handle_message)
        self._channel.on("error", self._handle_error)
        self._channel.on(
            "reconnecting", lambda: logger.info("Feishu WS reconnecting...")
        )
        self._channel.on(
            "reconnected", lambda: logger.info("Feishu WS reconnected")
        )

        # Start in background and wait until the connection is ready
        await self._channel.connect_until_ready(timeout=30)
        logger.info("FeishuAdapter started (app_id=%s)", self._app_id)

    async def send_message(self, user_id: str, text: str) -> SendResult:
        """Send a text message to a Feishu user.

        Args:
            user_id: Recipient's open_id (ou_xxx).
            text: Message text to send.

        Returns:
            SendResult indicating success/failure. Never raises.
        """
        if not text or not text.strip():
            return SendResult(success=False, error="empty text")

        if not self._channel:
            return SendResult(success=False, error="Feishu channel not started")

        try:
            # Prefer chat_id from the map (ensures the message lands in the
            # right conversation context).  Fallback to open_id which the
            # SDK auto-detects via the ID prefix (ou_ -> open_id).
            receive_id = self._chat_map.get(user_id) or user_id

            result = await self._channel.send(receive_id, {"text": text})

            if result.success:
                return SendResult(success=True, message_id=result.message_id)
            else:
                err_msg = str(result.error) if result.error else "unknown error"
                return SendResult(success=False, error=err_msg)

        except Exception as e:
            logger.error("send_message failed to %s: %s", user_id, e)
            return SendResult(success=False, error=str(e))

    async def stop(self) -> None:
        """Disconnect WebSocket and clean up resources."""
        self._running = False
        if self._channel:
            await self._channel.disconnect()
            self._channel = None
        logger.info("FeishuAdapter stopped")

    # ---- internal ----

    async def _handle_message(self, msg: InboundMessage) -> None:
        """Process an incoming message dispatched by FeishuChannel."""
        if not self._on_message or not self._running:
            return

        sender_id = msg.sender_id     # open_id (ou_xxx)
        chat_id = msg.chat_id         # chat_id (oc_xxx)
        chat_type = msg.chat_type     # "group" or "p2p"
        text = (msg.content_text or "").strip()
        message_id = msg.message_id

        if not text:
            return  # non-text message (image, file, card, etc.)

        # Group chat @-mention gating
        if chat_type == "group" and self._require_mention:
            if not msg.mentioned_bot:
                return  # bot not mentioned, skip

            # Strip the @bot mention placeholder(s) from text prefix
            text = _strip_bot_mention(text, msg.mentions)

        # Update chat_map so send_message() can route replies to this chat
        if chat_id and sender_id:
            self._chat_map[sender_id] = chat_id

        event = MessageEvent(
            user_id=sender_id,
            text=text,
            message_id=message_id,
        )

        try:
            await self._on_message(event)
        except Exception as e:
            logger.error(
                "on_message handler failed for %s: %s", sender_id, e
            )

    async def _handle_error(self, err: Exception) -> None:
        """Log FeishuChannel errors."""
        logger.error("FeishuChannel error: %s", err)


def _strip_bot_mention(text: str, mentions: list) -> str:
    """Strip @bot mention placeholders from the beginning of text.

    Feishu's content_text uses mention keys like ``@_user_1`` as
    placeholders.  This function removes the bot's mention key prefix
    so downstream code gets clean user text.
    """
    for m in mentions or []:
        if m.is_bot and m.key and text.startswith(m.key):
            text = text[len(m.key) :].strip()
            break
    return text
