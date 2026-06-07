"""
agent-channel — Channel adapter layer for AI agent platform integration.

Provides a unified interface for connecting AI agents to messaging
platforms via channel adapters (WeChat, Telegram, etc.).
"""

from .account_store import AccountCredential, AccountStore
from .channel_adapter import ChannelAdapter, MessageEvent, SendResult
from .feishu_login import feishu_login, main as feishu_login_main
from .login import qr_login, main as login_main
from .runtime import AgentRuntime
from .session import SessionContext
from .utils import setup_logging
from .feishu_adapter import FeishuAdapter
from .wechat_adapter import WeChatAdapter

__all__ = [
    "AccountCredential",
    "AccountStore",
    "ChannelAdapter",
    "FeishuAdapter",
    "feishu_login",
    "feishu_login_main",
    "login_main",
    "MessageEvent",
    "qr_login",
    "SendResult",
    "SessionContext",
    "AgentRuntime",
    "setup_logging",
    "WeChatAdapter",
]
