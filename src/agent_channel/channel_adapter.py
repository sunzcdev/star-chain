"""Channel adapter interface — abstract base for all platform adapters."""

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
        """Start listening for incoming messages.

        Args:
            on_message: Async callback invoked on each incoming MessageEvent.
        """
        pass

    @abstractmethod
    async def send_message(self, user_id: str, text: str) -> SendResult:
        """Send a text message to a user.

        Args:
            user_id: Platform user identifier.
            text: Message text to send.

        Returns:
            SendResult indicating success/failure.
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop listening and clean up resources."""
        pass
