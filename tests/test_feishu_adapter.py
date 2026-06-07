"""Mock tests for FeishuAdapter — covers all core behaviors without real lark-oapi connection."""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_project_root = __file__.rsplit("/", 2)[0]
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from src.agent_channel.channel_adapter import MessageEvent, SendResult
from src.agent_channel.feishu_adapter import FeishuAdapter
from lark_oapi.channel.types import Conversation, Identity, InboundMessage, Mention


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_inbound_msg(
    msg_id: str = "msg_001",
    chat_id: str = "oc_test_chat",
    chat_type: str = "p2p",
    sender_id: str = "ou_test_user",
    text: str = "hello",
    mentioned_bot: bool = False,
    mentions: list | None = None,
) -> InboundMessage:
    return InboundMessage(
        id=msg_id,
        create_time=1234567890,
        conversation=Conversation(chat_id=chat_id, chat_type=chat_type),
        sender=Identity(open_id=sender_id),
        mentions=mentions or [],
        mentioned_bot=mentioned_bot,
        content_text=text,
    )


def _mocked_channel() -> MagicMock:
    """Return a MagicMock FeishuChannel instance with async methods stubbed."""
    ch = MagicMock()
    ch.connect_until_ready = AsyncMock()
    ch.disconnect = AsyncMock()
    ch.send = AsyncMock(return_value=MagicMock(success=True, message_id="mid_001", error=None))
    return ch


# ---------------------------------------------------------------------------
# 1 — import / instantiation
# ---------------------------------------------------------------------------

def test_feishu_adapter_instantiation():
    """FeishuAdapter can be instantiated with app_id and app_secret."""
    adapter = FeishuAdapter(app_id="test-id", app_secret="test-secret")
    assert adapter._app_id == "test-id"
    assert adapter._app_secret == "test-secret"
    assert adapter._require_mention is True
    assert adapter._running is False
    assert adapter._channel is None
    assert adapter._chat_map == {}
    print("✓ test_feishu_adapter_instantiation PASSED")


# ---------------------------------------------------------------------------
# 2 — start / stop lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_stop_lifecycle():
    """start() creates FeishuChannel & registers handlers; stop() disconnects."""
    with patch("src.agent_channel.feishu_adapter.FeishuChannel") as MockChannel:
        ch = _mocked_channel()
        MockChannel.return_value = ch

        adapter = FeishuAdapter(app_id="test-id", app_secret="test-secret")
        events = []

        async def fake_cb(event: MessageEvent):
            events.append(event)

        await adapter.start(fake_cb)

        # FeishuChannel constructed with the right args
        MockChannel.assert_called_once_with(app_id="test-id", app_secret="test-secret")
        # Event handlers registered (message + error + reconnecting/reconnected)
        assert ch.on.call_count >= 2
        assert adapter._running is True
        ch.connect_until_ready.assert_awaited_once()

        await adapter.stop()

        assert adapter._running is False
        ch.disconnect.assert_awaited_once()
        assert adapter._channel is None
    print("✓ test_start_stop_lifecycle PASSED")


# ---------------------------------------------------------------------------
# 3 — p2p message processing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_p2p_message_processing():
    """P2P message triggers callback with correctly-constructed MessageEvent."""
    with patch("src.agent_channel.feishu_adapter.FeishuChannel") as MockChannel:
        MockChannel.return_value = _mocked_channel()

        adapter = FeishuAdapter(app_id="test-id", app_secret="test-secret")
        events = []

        async def fake_cb(event: MessageEvent):
            events.append(event)

        await adapter.start(fake_cb)

        msg = _make_inbound_msg(
            msg_id="msg_p2p",
            chat_id="oc_p2p_chat",
            chat_type="p2p",
            sender_id="ou_alice",
            text="你好",
        )
        await adapter._handle_message(msg)

        assert len(events) == 1
        ev = events[0]
        assert ev.user_id == "ou_alice"
        assert ev.text == "你好"
        assert ev.message_id == "msg_p2p"

        await adapter.stop()
    print("✓ test_p2p_message_processing PASSED")


# ---------------------------------------------------------------------------
# 4 — group chat @bot (should trigger callback)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_group_chat_mention_bot():
    """Group message with @bot mention triggers callback."""
    with patch("src.agent_channel.feishu_adapter.FeishuChannel") as MockChannel:
        MockChannel.return_value = _mocked_channel()

        adapter = FeishuAdapter(app_id="test-id", app_secret="test-secret")
        events = []

        async def fake_cb(event: MessageEvent):
            events.append(event)

        await adapter.start(fake_cb)

        # Simulate group chat with @bot mention
        bot_mention = Mention(key="@_user_1", open_id="ou_bot", is_bot=True)
        msg = _make_inbound_msg(
            msg_id="msg_group_bot",
            chat_id="oc_group",
            chat_type="group",
            sender_id="ou_bob",
            text="@_user_1 帮我查天气",
            mentioned_bot=True,
            mentions=[bot_mention],
        )
        await adapter._handle_message(msg)

        assert len(events) == 1
        ev = events[0]
        assert ev.user_id == "ou_bob"
        # The @bot prefix should be stripped
        assert ev.text == "帮我查天气"
        assert ev.message_id == "msg_group_bot"

        await adapter.stop()
    print("✓ test_group_chat_mention_bot PASSED")


# ---------------------------------------------------------------------------
# 5 — group chat no @bot (should NOT trigger callback)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_group_chat_no_mention():
    """Group message without @bot mention does NOT trigger callback."""
    with patch("src.agent_channel.feishu_adapter.FeishuChannel") as MockChannel:
        MockChannel.return_value = _mocked_channel()

        adapter = FeishuAdapter(app_id="test-id", app_secret="test-secret")
        events = []

        async def fake_cb(event: MessageEvent):
            events.append(event)

        await adapter.start(fake_cb)

        msg = _make_inbound_msg(
            msg_id="msg_group_no_bot",
            chat_id="oc_group",
            chat_type="group",
            sender_id="ou_bob",
            text="普通群聊消息",
            mentioned_bot=False,
            mentions=[],  # no bot mention
        )
        await adapter._handle_message(msg)

        # No callback should have been called
        assert len(events) == 0

        await adapter.stop()
    print("✓ test_group_chat_no_mention PASSED")


# ---------------------------------------------------------------------------
# 6 — group chat strip @mention prefix
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_group_chat_strip_mention_prefix():
    """content_text has @bot mention prefix stripped before callback."""
    with patch("src.agent_channel.feishu_adapter.FeishuChannel") as MockChannel:
        MockChannel.return_value = _mocked_channel()

        adapter = FeishuAdapter(app_id="test-id", app_secret="test-secret")
        events = []

        async def fake_cb(event: MessageEvent):
            events.append(event)

        await adapter.start(fake_cb)

        bot_mention = Mention(key="@_user_1", open_id="ou_bot", is_bot=True)
        # Multiple @mentions — only the bot's should be stripped
        msg = _make_inbound_msg(
            msg_id="msg_strip",
            chat_id="oc_group",
            chat_type="group",
            sender_id="ou_carol",
            text="@_user_1 @_user_2 大家注意",
            mentioned_bot=True,
            mentions=[bot_mention, Mention(key="@_user_2", open_id="ou_user2", is_bot=False)],
        )
        await adapter._handle_message(msg)

        assert len(events) == 1
        ev = events[0]
        # Only the bot's @mention is stripped
        assert ev.text == "@_user_2 大家注意"

        await adapter.stop()
    print("✓ test_group_chat_strip_mention_prefix PASSED")


# ---------------------------------------------------------------------------
# 7 — send_message success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_message_success():
    """send_message calls channel.send with correct arguments."""
    with patch("src.agent_channel.feishu_adapter.FeishuChannel") as MockChannel:
        ch = _mocked_channel()
        MockChannel.return_value = ch

        adapter = FeishuAdapter(app_id="test-id", app_secret="test-secret")

        async def fake_cb(_event):
            pass

        await adapter.start(fake_cb)

        result = await adapter.send_message("ou_target", "测试回复")

        assert result.success is True
        assert result.message_id == "mid_001"
        ch.send.assert_awaited_once_with("ou_target", {"text": "测试回复"})

        await adapter.stop()
    print("✓ test_send_message_success PASSED")


# ---------------------------------------------------------------------------
# 8 — send_message empty text
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_message_empty_text():
    """Empty text returns SendResult(success=False) without calling channel.send."""
    with patch("src.agent_channel.feishu_adapter.FeishuChannel") as MockChannel:
        ch = _mocked_channel()
        MockChannel.return_value = ch

        adapter = FeishuAdapter(app_id="test-id", app_secret="test-secret")

        async def fake_cb(_event):
            pass

        await adapter.start(fake_cb)

        # Empty string
        r1 = await adapter.send_message("ou_target", "")
        assert r1.success is False
        assert r1.error == "empty text"

        # Whitespace-only
        r2 = await adapter.send_message("ou_target", "   ")
        assert r2.success is False
        assert r2.error == "empty text"

        # channel.send should never have been called for empty texts
        ch.send.assert_not_called()

        await adapter.stop()
    print("✓ test_send_message_empty_text PASSED")


# ---------------------------------------------------------------------------
# 9 — chat_map update
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_map_update():
    """After receiving a message, chat_map records chat_id; send_message prefers chat_id."""
    with patch("src.agent_channel.feishu_adapter.FeishuChannel") as MockChannel:
        ch = _mocked_channel()
        MockChannel.return_value = ch

        adapter = FeishuAdapter(app_id="test-id", app_secret="test-secret")

        async def fake_cb(_event):
            pass

        await adapter.start(fake_cb)

        # Receive a message — should populate chat_map
        msg = _make_inbound_msg(
            msg_id="msg_map",
            chat_id="oc_group_convo",
            chat_type="group",
            sender_id="ou_alice",
            text="帮我查天气",
            mentioned_bot=True,
            mentions=[Mention(key="@_user_1", open_id="ou_bot", is_bot=True)],
        )
        await adapter._handle_message(msg)

        # chat_map should now have the mapping
        assert adapter._chat_map.get("ou_alice") == "oc_group_convo"

        # send_message should use chat_id from the map (oc_...) instead of open_id (ou_...)
        await adapter.send_message("ou_alice", "回复")

        # Verify that send was called with the chat_id, not the open_id
        call_arg = ch.send.await_args_list[-1].args[0]
        assert call_arg == "oc_group_convo", f"Expected chat_id, got {call_arg}"

        await adapter.stop()
    print("✓ test_chat_map_update PASSED")
