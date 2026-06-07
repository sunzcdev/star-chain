#!/usr/bin/env python3
"""
两步 QR 登录脚本：
1. 获取二维码 URL，输出到 stdout（Manager 捕获并转发给用户）
2. 轮询等待扫码确认，保存凭证到 AccountStore

用法：
  python3 qr_login_runner.py
"""
import asyncio
import json
import sys
import os

# 确保能 import agent_channel
sys.path.insert(0, os.path.expanduser("~/projects/agent-channel"))

from src.agent_channel.account_store import AccountStore
from src.agent_channel.login import qr_login

async def main():
    store = AccountStore()
    
    # 检查已有账号，直接复用
    existing = store.list_accounts()
    if existing:
        cred = existing[0]
        print(json.dumps({
            "phase": "reuse",
            "account_id": cred.account_id,
            "user_id": cred.user_id,
            "saved_at": cred.saved_at,
        }))
        return 0
    
    # 执行扫码登录（超时 480 秒 = 8 分钟）
    # qr_login 内部会输出二维码 URL 和 ASCII 码到 stderr
    credential = await qr_login(
        account_store=store,
        bot_type="3",
        timeout_seconds=480,
        json_mode=False,
    )
    
    if credential is None:
        print(json.dumps({"phase": "failed", "error": "login_failed"}))
        return 1
    
    print(json.dumps({
        "phase": "success",
        "account_id": credential.account_id,
        "user_id": credential.user_id,
        "saved_at": credential.saved_at,
    }))
    return 0

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
