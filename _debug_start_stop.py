"""Debug the start_stop test hang."""
import asyncio
import sys
sys.path.insert(0, "/home/ubuntu/projects/agent-channel")
from unittest.mock import AsyncMock, patch


async def main():
    from src.agent_channel.wechat_adapter import WeChatAdapter
    
    adapter = WeChatAdapter(token="test-token", account_id="test-account")
    
    async def fake_on_message(_event):
        pass
    
    async def mock_get_updates(*args, **kwargs):
        return {"ret": 0, "msgs": [], "get_updates_buf": kwargs.get("sync_buf", "")}
    
    with patch("src.agent_channel.wechat_adapter._get_updates", new=mock_get_updates):
        print("Calling start()...")
        await adapter.start(on_message=fake_on_message)
        print("start() done, sleeping 0.15...")
        await asyncio.sleep(0.15)
        print("Calling stop()...")
        await adapter.stop()
        print("stop() done")
    
    print("✓ PASSED")


asyncio.run(main())
