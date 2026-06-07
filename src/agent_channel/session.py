"""Session context — per-user conversation history persisted as JSON files."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# Maximum number of messages kept in history.
MAX_HISTORY_SIZE = 50


class SessionContext:
    """Per-user conversation session backed by a JSON file.

    Each user has one JSON file at ``{storage_dir}/{user_id}.json``.
    The file stores the conversation history plus creation/update timestamps.

    History is capped at ``MAX_HISTORY_SIZE`` messages. When the limit is
    reached, the oldest messages are trimmed on save.
    """

    def __init__(self, user_id: str, storage_dir: str | Path) -> None:
        self._user_id = user_id
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._file_path = self._storage_dir / f"{user_id}.json"
        self._history: List[dict] = []
        self._loaded = False

    # ---- public API ----

    @property
    def history(self) -> List[dict]:
        """Conversation history as OpenAI message dicts (role/content)."""
        self._ensure_loaded()
        return list(self._history)

    def add_user_message(self, text: str) -> None:
        """Append a user message to history."""
        self._ensure_loaded()
        self._history.append({"role": "user", "content": text})

    def add_assistant_message(self, text: str) -> None:
        """Append an assistant message to history."""
        self._ensure_loaded()
        self._history.append({"role": "assistant", "content": text})

    def save(self) -> None:
        """Persist history to the JSON file, trimming to MAX_HISTORY_SIZE."""
        self._ensure_loaded()
        self._trim_history()
        data = {
            "user_id": self._user_id,
            "created_at": self._get_or_create_created_at(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "history": self._history,
        }
        self._file_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def clear(self) -> None:
        """Reset history to empty (used by /new command)."""
        self._history = []
        self.save()

    def reset(self) -> None:
        """Alias for clear — used by runtime.new_session()."""
        self.clear()

    # ---- internal ----

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self._file_path.exists():
            try:
                data = json.loads(self._file_path.read_text(encoding="utf-8"))
                self._history = data.get("history", [])
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("session: failed to load %s: %s, starting fresh", self._file_path, e)
                self._history = []

    def _trim_history(self) -> None:
        if len(self._history) > MAX_HISTORY_SIZE:
            excess = len(self._history) - MAX_HISTORY_SIZE
            self._history = self._history[excess:]

    def _get_or_create_created_at(self) -> str:
        if self._file_path.exists():
            try:
                data = json.loads(self._file_path.read_text(encoding="utf-8"))
                return data.get("created_at", datetime.now(timezone.utc).isoformat())
            except (json.JSONDecodeError, OSError):
                pass
        return datetime.now(timezone.utc).isoformat()
