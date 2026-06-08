"""账号凭证管理 — AccountStore 模块。"""

import json
import logging
import time
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_STORAGE_ROOT = "~/.star-chain/feishu/accounts"


class CredentialType(str, Enum):
    WECHAT = "wechat"
    FEISHU = "feishu"


@dataclass
class AccountCredential:
    account_id: str
    credential_type: str = "feishu"

    # WeChat 字段
    token: str = ""            # ilink_bot_token
    base_url: str = ""         # iLink API 地址

    # Feishu 字段
    app_secret: str = ""
    domain: str = "feishu.cn"
    user_name: str = ""
    avatar_url: str = ""

    # 通用
    user_id: str = ""
    saved_at: str = ""


class AccountStore:
    """磁盘账号凭证管理。"""

    def __init__(self, storage_root: str = DEFAULT_STORAGE_ROOT):
        self._root = Path(storage_root).expanduser()
        self._root.mkdir(parents=True, exist_ok=True)

    def list_accounts(self) -> list[AccountCredential]:
        accounts = []
        for f in sorted(self._root.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                accounts.append(AccountCredential(**data))
            except Exception as e:
                logger.warning("account_store: corrupt file %s: %s", f.name, e)
        return accounts

    def load_account(self, account_id: str) -> Optional[AccountCredential]:
        path = self._file_path(account_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return AccountCredential(**data)
        except Exception as e:
            logger.warning("account_store: failed to load %s: %s", account_id, e)
            return None

    def save_credential(self, credential: AccountCredential) -> AccountCredential:
        path = self._file_path(credential.account_id)
        path.write_text(
            json.dumps(asdict(credential), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        path.chmod(0o600)
        return credential

    def delete_account(self, account_id: str) -> bool:
        path = self._file_path(account_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def _file_path(self, account_id: str) -> Path:
        return self._root / f"{account_id}.json"
