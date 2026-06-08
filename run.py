#!/usr/bin/env python3
"""StarChain — 入口。飞书 + 微信双渠道 + AgentRuntime 的三 Agent 协作。

如果使用系统 Python 直接执行，本脚本会自动检测并重新使用项目的
.venv 虚拟环境，确保依赖（如 lark_oapi.channel）可被正确加载。
"""

import asyncio
import logging
import os
import signal
import sys

_project_root = os.path.dirname(os.path.abspath(__file__))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

_venv_python = os.path.join(_project_root, ".venv", "bin", "python3")
if os.path.exists(_venv_python) and sys.executable != _venv_python:
    os.execv(_venv_python, [_venv_python, __file__, *sys.argv[1:]])

from src.star_chain.account_store import AccountStore
from src.star_chain.feishu_adapter import FeishuAdapter
from src.star_chain.wechat_adapter import WeChatAdapter
from src.star_chain.runtime import AgentRuntime
from src.star_chain.utils import setup_logging

logger = logging.getLogger(__name__)

WECHAT_ACCOUNT_DIR = "~/.agent-channel/weixin/accounts"
FEISHU_ACCOUNT_DIR = "~/.star-chain/feishu/accounts"


def _get_feishu_config() -> dict:
    """获取飞书凭证：优先 AccountStore，回退到环境变量。"""
    store = AccountStore(FEISHU_ACCOUNT_DIR)
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


def _get_wechat_config() -> dict | None:
    """获取微信凭证：从 AccountStore 加载。"""
    store = AccountStore(WECHAT_ACCOUNT_DIR)
    accounts = store.list_accounts()
    if accounts:
        cred = accounts[0]
        logger.info("loaded weixin credentials from AccountStore: %s", cred.account_id)
        return {
            "token": cred.token,
            "account_id": cred.account_id,
            "base_url": cred.base_url or "https://ilinkai.weixin.qq.com",
        }
    logger.warning("微信凭证未配置 — 目录 %s 为空", WECHAT_ACCOUNT_DIR)
    return None


async def main():
    setup_logging()

    session_dir = os.environ.get("STAR_CHAIN_SESSION_DIR", "~/.star-chain/sessions")
    max_turns = int(os.environ.get("STAR_CHAIN_MAX_TURNS", "30"))

    logger.info("Initializing AgentRuntime ...")
    runtime = AgentRuntime(
        max_turns=max_turns,
        session_dir=session_dir,
    )

    # 所有渠道共享同一个消息处理
    async def on_message(adapter, event):
        if event.text.strip().lower() in ("/stop", "/quit"):
            logger.info("stop command received, shutting down")
            asyncio.get_event_loop().stop()
            return
        logger.info("received message from %s: %s...", event.user_id, event.text[:60])
        response = await runtime.handle_message(event.user_id, event.text)
        await adapter.send_message(event.user_id, response)
        logger.info("sent response to %s: %s...", event.user_id, response[:60])

    adapters = []

    # 1. 飞书渠道
    feishu_cfg = _get_feishu_config()
    if feishu_cfg["feishu_app_id"]:
        logger.info("Initializing FeishuAdapter ...")
        feishu_adapter = FeishuAdapter(
            app_id=feishu_cfg["feishu_app_id"],
            app_secret=feishu_cfg["feishu_app_secret"],
        )
        adapters.append(("feishu", feishu_adapter))
    else:
        logger.warning("飞书凭证缺失，跳过飞书渠道")

    # 2. 微信渠道
    wechat_cfg = _get_wechat_config()
    if wechat_cfg:
        logger.info("Initializing WeChatAdapter ...")
        wechat_adapter = WeChatAdapter(
            token=wechat_cfg["token"],
            account_id=wechat_cfg["account_id"],
            base_url=wechat_cfg["base_url"],
        )
        adapters.append(("weixin", wechat_adapter))
    else:
        logger.warning("微信凭证缺失，跳过微信渠道")

    if not adapters:
        logger.error("所有渠道凭证均缺失，退出")
        sys.exit(1)

    # 3. 启动所有渠道
    for name, adapter in adapters:
        logger.info("Starting %s adapter ...", name)
        await adapter.start(lambda event, a=adapter: on_message(a, event))

    logger.info(
        "StarChain started — listening via %s",
        ", ".join(name for name, _ in adapters),
    )

    # 4. 等待退出信号
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

    # 5. 清理
    logger.info("shutting down ...")
    for _, adapter in adapters:
        await adapter.stop()

    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    logger.info("shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
