"""WeChat adapter — iLink Bot API (long-poll) integration.

Reuses the pure-function iLink communication layer from Hermes weixin.py
without depending on Hermes' BasePlatformAdapter framework.
"""

import asyncio
import json
import logging
import secrets
import struct
import base64
from typing import Any, Callable, Optional

import aiohttp

from .channel_adapter import ChannelAdapter, MessageEvent, SendResult

logger = logging.getLogger(__name__)

# ---- iLink API constants ----

ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
ILINK_APP_ID = "bot"
CHANNEL_VERSION = "2.2.0"
ILINK_APP_CLIENT_VERSION = (2 << 16) | (2 << 8) | 0

EP_GET_UPDATES = "ilink/bot/getupdates"
EP_SEND_MESSAGE = "ilink/bot/sendmessage"
EP_SEND_TYPING = "ilink/bot/sendtyping"

LONG_POLL_TIMEOUT_MS = 35_000
API_TIMEOUT_MS = 15_000

ITEM_TEXT = 1
MSG_TYPE_BOT = 2
MSG_STATE_FINISH = 2

# ---- iLink API utility functions (reused from Hermes weixin.py) ----


def _json_dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _random_wechat_uin() -> str:
    value = struct.unpack(">I", secrets.token_bytes(4))[0]
    return base64.b64encode(str(value).encode("utf-8")).decode("ascii")


def _base_info() -> dict:
    return {"channel_version": CHANNEL_VERSION}


def _headers(token: Optional[str], body: str) -> dict:
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Content-Length": str(len(body.encode("utf-8"))),
        "X-WECHAT-UIN": _random_wechat_uin(),
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def _api_post(
    session: aiohttp.ClientSession,
    *,
    base_url: str,
    endpoint: str,
    payload: dict,
    token: Optional[str],
    timeout_ms: int,
) -> dict:
    body = _json_dumps({**payload, "base_info": _base_info()})
    url = f"{base_url.rstrip('/')}/{endpoint}"
    timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000)
    async with session.post(
        url, data=body, headers=_headers(token, body), timeout=timeout
    ) as response:
        raw = await response.text()
        if not response.ok:
            raise RuntimeError(
                f"iLink POST {endpoint} HTTP {response.status}: {raw[:200]}"
            )
        return json.loads(raw)


async def _get_updates(
    session: aiohttp.ClientSession,
    *,
    base_url: str,
    token: str,
    sync_buf: str,
    timeout_ms: int,
) -> dict:
    try:
        return await _api_post(
            session,
            base_url=base_url,
            endpoint=EP_GET_UPDATES,
            payload={"get_updates_buf": sync_buf},
            token=token,
            timeout_ms=timeout_ms,
        )
    except asyncio.TimeoutError:
        return {"ret": 0, "msgs": [], "get_updates_buf": sync_buf}


async def _send_message(
    session: aiohttp.ClientSession,
    *,
    base_url: str,
    token: str,
    to: str,
    text: str,
    context_token: Optional[str],
    client_id: str,
) -> dict:
    if not text or not text.strip():
        raise ValueError("_send_message: text must not be empty")
    message: dict = {
        "from_user_id": "",
        "to_user_id": to,
        "client_id": client_id,
        "message_type": MSG_TYPE_BOT,
        "message_state": MSG_STATE_FINISH,
        "item_list": [{"type": ITEM_TEXT, "text_item": {"text": text}}],
    }
    if context_token:
        message["context_token"] = context_token
    return await _api_post(
        session,
        base_url=base_url,
        endpoint=EP_SEND_MESSAGE,
        payload={"msg": message},
        token=token,
        timeout_ms=API_TIMEOUT_MS,
    )


def _extract_text(item_list: list[dict]) -> Optional[str]:
    """Extract text content from an iLink message item list."""
    for item in item_list:
        if item.get("type") == ITEM_TEXT:
            text = str((item.get("text_item") or {}).get("text") or "")
            if text:
                return text
    return None


# ---- WeChatAdapter ----


class WeChatAdapter(ChannelAdapter):
    """WeChat adapter — connects to iLink Bot API via long-polling.

    Usage::

        adapter = WeChatAdapter(token="...", account_id="...")
        await adapter.start(on_message_callback)
        # ... run ... 
        await adapter.stop()
    """

    def __init__(
        self,
        token: str,
        account_id: str,
        base_url: str = ILINK_BASE_URL,
    ) -> None:
        self._token = token
        self._account_id = account_id
        self._base_url = base_url
        self._session: Optional[aiohttp.ClientSession] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._running = False
        self._on_message: Optional[Callable] = None
        self._sync_buf = ""

    async def start(
        self, on_message: Callable[[MessageEvent], None]
    ) -> None:
        """Start long-polling for incoming messages."""
        self._on_message = on_message
        self._running = True
        self._session = aiohttp.ClientSession()
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info(
            "WeChatAdapter started (account=%s, base=%s)",
            self._account_id, self._base_url,
        )

    async def send_message(self, user_id: str, text: str) -> SendResult:
        """Send a text message to a WeChat user via iLink."""
        try:
            resp = await _send_message(
                self._session,
                base_url=self._base_url,
                token=self._token,
                to=user_id,
                text=text,
                context_token=None,
                client_id=f"agent-channel-{user_id}",
            )
            errcode = resp.get("errcode", 0)
            if errcode != 0:
                logger.warning(
                    "send_message to %s returned errcode=%s: %s",
                    user_id, errcode, resp.get("errmsg", ""),
                )
            return SendResult(
                success=(errcode == 0),
                error=resp.get("errmsg") if errcode != 0 else None,
            )
        except Exception as e:
            logger.error("send_message failed to %s: %s", user_id, e)
            return SendResult(success=False, error=str(e))

    async def stop(self) -> None:
        """Stop polling and clean up resources."""
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self._session:
            await self._session.close()
        logger.info("WeChatAdapter stopped")

    # ---- internal ----

    async def _poll_loop(self) -> None:
        """Long-poll iLink for new messages."""
        while self._running:
            try:
                response = await _get_updates(
                    self._session,
                    base_url=self._base_url,
                    token=self._token,
                    sync_buf=self._sync_buf,
                    timeout_ms=LONG_POLL_TIMEOUT_MS,
                )

                # Update sync buffer for stateful polling
                new_buf = response.get("get_updates_buf", "")
                if new_buf:
                    self._sync_buf = new_buf

                # Process messages
                for msg in response.get("msgs", []):
                    await self._process_message(msg)

            except asyncio.CancelledError:
                break
            except asyncio.TimeoutError:
                continue  # normal long-poll timeout
            except Exception as e:
                logger.error("poll_loop error: %s", e)
                await asyncio.sleep(2)

    async def _process_message(self, msg: dict) -> None:
        """Parse and dispatch a single iLink message."""
        text = _extract_text(msg.get("item_list", []))
        if not text:
            return

        from_user = msg.get("from_user_id", "")
        if not from_user:
            logger.warning("received message without from_user_id")
            return

        event = MessageEvent(
            user_id=from_user,
            text=text,
            message_id=msg.get("message_id", ""),
        )

        if self._on_message:
            try:
                await self._on_message(event)
            except Exception as e:
                logger.error(
                    "on_message handler failed for %s: %s", from_user, e,
                )
