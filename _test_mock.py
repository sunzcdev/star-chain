"""Temporary repro script for mock setup."""
import asyncio
from unittest.mock import AsyncMock


async def main():
    mock_resp = AsyncMock(spec_set=["ok", "text"])
    mock_resp.ok = True
    mock_resp.text = AsyncMock(return_value='{"errcode": 0, "errmsg": "ok"}')

    post_cm = AsyncMock()
    post_cm.__aenter__.return_value = mock_resp

    session_mock = AsyncMock()
    session_mock.post.return_value = post_cm

    result = session_mock.post("url", data="body", headers={}, timeout=None)
    print(f"post() returned: {type(result).__name__}")
    print(f"Is AsyncMock: {isinstance(result, AsyncMock)}")
    try:
        async with result as resp:
            print(f"Got response: {resp}")
            txt = await resp.text()
            print(f"text: {txt}")
    except Exception as e:
        print(f"Error: {e}")


asyncio.run(main())
