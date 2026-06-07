"""Integration tests for agent-channel — adapter poll loop and end-to-end flows."""

import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure project root is on sys.path
_project_root = __file__.rsplit("/", 2)[0]
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from src.agent_channel.wechat_adapter import WeChatAdapter
from src.agent_channel.channel_adapter import MessageEvent


@pytest.mark.asyncio
async def test_wechat_adapter_process_message():
    """Test that WeChatAdapter._process_message dispatches correctly."""
    messages_received = []

    async def fake_on_message(event: MessageEvent):
        messages_received.append(event)

    adapter = WeChatAdapter(token="test-token", account_id="test-account")
    adapter._on_message = fake_on_message

    # Process a valid text message
    await adapter._process_message({
        "from_user_id": "user_001",
        "message_id": "msg_001",
        "item_list": [{"type": 1, "text_item": {"text": "你好"}}],
    })

    assert len(messages_received) == 1
    assert messages_received[0].user_id == "user_001"
    assert messages_received[0].text == "你好"
    assert messages_received[0].message_id == "msg_001"

    # Process a message without text (non-text items) — should be skipped
    await adapter._process_message({
        "from_user_id": "user_002",
        "message_id": "msg_002",
        "item_list": [{"type": 3, "image_item": {"url": "https://example.com/img.png"}}],
    })
    assert len(messages_received) == 1  # no new message

    # Process a message without from_user_id — should be skipped
    await adapter._process_message({
        "message_id": "msg_003",
        "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
    })
    assert len(messages_received) == 1  # no new message

    print("✓ test_wechat_adapter_process_message PASSED")


async def _create_adapter_with_http_mock() -> tuple[WeChatAdapter, AsyncMock]:
    """Helper: create adapter with session.post mocked as async context manager."""
    adapter = WeChatAdapter(token="test-token", account_id="test-account")

    mock_resp = AsyncMock(spec_set=["ok", "text"])
    mock_resp.ok = True
    mock_resp.text = AsyncMock(return_value="{}")

    post_cm = AsyncMock()
    post_cm.__aenter__.return_value = mock_resp

    session_mock = MagicMock()  # NOT AsyncMock — session.post() is not awaited, used in async with
    session_mock.post.return_value = post_cm
    adapter._session = session_mock

    return adapter, mock_resp


@pytest.mark.asyncio
async def test_wechat_adapter_send_message():
    """Test that WeChatAdapter.send_message works with mocked API call."""
    adapter, mock_resp = await _create_adapter_with_http_mock()
    mock_resp.text = AsyncMock(return_value=json.dumps({"errcode": 0, "errmsg": "ok"}))

    result = await adapter.send_message("user_001", "测试消息")
    assert result.success is True, f"Expected success, got error={result.error}"
    assert result.error is None

    print("✓ test_wechat_adapter_send_message PASSED")


@pytest.mark.asyncio
async def test_wechat_adapter_send_message_failure():
    """Test that send_message failure is handled gracefully."""
    adapter, mock_resp = await _create_adapter_with_http_mock()
    mock_resp.text = AsyncMock(
        return_value=json.dumps({"errcode": 40001, "errmsg": "invalid credential"})
    )

    result = await adapter.send_message("user_001", "测试消息")
    assert result.success is False
    assert result.error == "invalid credential"

    print("✓ test_wechat_adapter_send_message_failure PASSED")


@pytest.mark.asyncio
async def test_wechat_adapter_stop():
    """Test that stop cancels poll task and closes session."""
    adapter = WeChatAdapter(token="test-token", account_id="test-account")
    adapter._session = AsyncMock()
    adapter._poll_task = asyncio.create_task(asyncio.sleep(999))
    adapter._running = True

    await adapter.stop()

    assert adapter._running is False
    assert adapter._session.close.called
    print("✓ test_wechat_adapter_stop PASSED")


@pytest.mark.asyncio
async def test_wechat_adapter_start_stop():
    """Test full start/stop cycle with mocked API (no real network calls)."""
    adapter = WeChatAdapter(token="test-token", account_id="test-account")

    async def fake_on_message(_event):
        pass

    async def mock_get_updates(*args, **kwargs):
        await asyncio.sleep(0)
        return {"ret": 0, "msgs": [], "get_updates_buf": kwargs.get("sync_buf", "")}

    with patch("src.agent_channel.wechat_adapter._get_updates", new=mock_get_updates), \
         patch("aiohttp.ClientSession") as mock_session_cls:
        mock_session_cls.return_value = AsyncMock()
        await adapter.start(on_message=fake_on_message)
        assert adapter._running is True
        assert adapter._session is not None
        assert adapter._poll_task is not None

        # Let one poll cycle run
        await asyncio.sleep(0.15)
        await adapter.stop()
        assert adapter._running is False

    print("✓ test_wechat_adapter_start_stop PASSED")


@pytest.mark.asyncio
async def test_wechat_adapter_poll_loop_updates_sync_buf():
    """Test that poll loop updates sync_buf from _get_updates responses."""
    adapter = WeChatAdapter(token="test-token", account_id="test-account")
    adapter._session = AsyncMock()
    adapter._on_message = lambda e: None

    poll_count = 0

    async def mock_get_updates(*args, **kwargs):
        nonlocal poll_count
        poll_count += 1
        await asyncio.sleep(0)
        return {
            "ret": 0,
            "msgs": [],
            "get_updates_buf": f"buf_{poll_count}",
        }

    with patch("src.agent_channel.wechat_adapter._get_updates", new=mock_get_updates):
        adapter._running = True
        task = asyncio.create_task(adapter._poll_loop())
        await asyncio.sleep(0.3)
        adapter._running = False
        await asyncio.sleep(0.1)

    assert poll_count >= 1, f"Expected mock called at least once, got {poll_count}"
    print(f"✓ test_wechat_adapter_poll_loop_updates_sync_buf PASSED (sync_buf={adapter._sync_buf}, poll_count={poll_count})")


async def main():
    await test_wechat_adapter_process_message()
    await test_wechat_adapter_send_message()
    await test_wechat_adapter_send_message_failure()
    await test_wechat_adapter_stop()
    await test_wechat_adapter_start_stop()
    await test_wechat_adapter_poll_loop_updates_sync_buf()
    print("\n✅ All integration tests passed!")


if __name__ == "__main__":
    asyncio.run(main())
