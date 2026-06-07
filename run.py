#!/usr/bin/env python3
"""StarChain — 入口。飞书渠道 + AgentRuntime 的三 Agent 协作。"""

import asyncio
import logging
import os
import signal
import sys

_project_root = os.path.dirname(os.path.abspath(__file__))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from src.star_chain.account_store import AccountStore
from src.star_chain.feishu_adapter import FeishuAdapter
from src.star_chain.runtime import AgentRuntime
from src.star_chain.utils import setup_logging

logger = logging.getLogger(__name__)


def _get_feishu_config() -> dict:
    """获取飞书凭证：优先 AccountStore，回退到环境变量。"""
    store = AccountStore("~/.star-chain/feishu/accounts")
    accounts = store.list_accounts()
    if accounts:
        cred = accounts[0]
        logger.info("loaded feishu credentials from AccountStore: %s", cred.account_id)
        return {"feishu_app_id": cred.account_id, "feishu_app_secret": cred.app_secret}

    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    if app_id and app_secret:
        return {"feishu_app_id": app_id, "feishu_app_secret": app_secret}

    logger.warning(
        "飞书凭证未配置 — 请先执行 `python -m src.star_chain.feishu_login` "
        "扫码登录，或设置 FEISHU_APP_ID / FEISHU_APP_SECRET 环境变量"
    )
    return {"feishu_app_id": "", "feishu_app_secret": ""}


async def main():
    setup_logging()

    feishu_cfg = _get_feishu_config()
    if not feishu_cfg["feishu_app_id"]:
        logger.error("飞书凭证缺失，退出")
        sys.exit(1)

    session_dir = os.environ.get("STAR_CHAIN_SESSION_DIR", "~/.star-chain/sessions")
    max_turns = int(os.environ.get("STAR_CHAIN_MAX_TURNS", "30"))

    logger.info("Initializing AgentRuntime ...")
    runtime = AgentRuntime(
        max_turns=max_turns,
        session_dir=session_dir,
    )

    # 2. 初始化飞书适配器
    logger.info("Initializing FeishuAdapter ...")
    adapter = FeishuAdapter(
        app_id=feishu_cfg["feishu_app_id"],
        app_secret=feishu_cfg["feishu_app_secret"],
    )

    # 3. 消息处理回调
    async def on_message(event):
        logger.info("received message from %s: %s...", event.user_id, event.text[:60])
        if event.text.strip().lower() in ("/stop", "/quit"):
            logger.info("stop command received, shutting down")
            asyncio.get_event_loop().stop()
            return
        response = await runtime.handle_message(event.user_id, event.text)
        await adapter.send_message(event.user_id, response)
        logger.info("sent response to %s: %s...", event.user_id, response[:60])

    # 4. 启动
    await adapter.start(on_message)

    logger.info("StarChain started — listening via Feishu (app_id=%s)", feishu_cfg["feishu_app_id"])

    # 5. 等待退出信号
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    await stop_event.wait()

    # 6. 清理
    logger.info("shutting down ...")
    await adapter.stop()
    logger.info("shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
