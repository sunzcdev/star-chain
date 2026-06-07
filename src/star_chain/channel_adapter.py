"""渠道适配器基类 — 所有平台适配器实现此接口。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class MessageEvent:
    """Incoming message from a platform."""
    user_id: str       # Platform user identifier
    text: str          # Message text content
    message_id: str    # Platform message ID


@dataclass
class SendResult:
    """Result of sending a message."""
    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None


class ChannelAdapter(ABC):
    """Abstract channel adapter — all platform adapters implement this interface.

    The interface is intentionally minimal:
    - start(): begin listening, callback on each incoming message
    - send_message(): push text to a user
    - stop(): clean shutdown
    """

    @abstractmethod
    async def start(self, on_message: Callable[[MessageEvent], None]) -> None:
        """Start listening for incoming messages."""
        pass

    @abstractmethod
    async def send_message(self, user_id: str, text: str) -> SendResult:
        """Send a text message to a user."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop listening and clean up resources."""
        pass
