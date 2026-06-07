"""账号凭证管理 — AccountStore 模块。

存储路径: ``{storage_root}/{account_id}.json``
每个文件包含一个 AccountCredential 的 JSON 表示，权限为 0o600。
"""

import json
import logging
import time
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 默认存储根目录
DEFAULT_STORAGE_ROOT = "~/.agent-channel/weixin/accounts"


class CredentialType(str, Enum):
    """支持的凭证类型。"""
    WECHAT = "wechat"
    FEISHU = "feishu"


@dataclass
class AccountCredential:
    """统一凭证模型——按 credential_type 解释各字段。"""
    account_id: str              # WeChat: ilink_bot_id / Feishu: app_id
    credential_type: str = "wechat"  # "wechat" | "feishu"

    # WeChat 字段
    token: str = ""              # bot_token
    base_url: str = ""           # iLink API 地址

    # Feishu 字段
    app_secret: str = ""         # app_secret（飞书应用密钥）
    domain: str = "feishu.cn"    # feishu.cn / larksuite.com
    user_name: str = ""          # 扫码用户的飞书昵称
    avatar_url: str = ""         # 扫码用户的头像 URL

    # 通用字段
    user_id: str = ""            # WeChat: ilink_user_id / Feishu: open_id
    saved_at: str = ""


class AccountStore:
    """磁盘账号凭证管理。

    存储路径: ``{storage_root}/{account_id}.json``
    每个文件包含一个 AccountCredential 的 JSON 表示。

    与 WeChatAdapter 解耦——WeChatAdapter 构造时从外部传入
    token/account_id，不直接依赖 AccountStore。
    """

    def __init__(self, storage_root: str = DEFAULT_STORAGE_ROOT):
        self._root = Path(storage_root).expanduser()
        self._root.mkdir(parents=True, exist_ok=True)

    # ---- 查询 ----

    def list_accounts(self) -> list[AccountCredential]:
        """列出所有已保存的账号凭证。"""
        accounts = []
        for f in sorted(self._root.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                accounts.append(AccountCredential(**data))
            except Exception as e:
                logger.warning("account_store: corrupt file %s: %s", f.name, e)
        return accounts

    def list_by_type(self, credential_type: str) -> list[AccountCredential]:
        """列出指定类型的所有凭证。"""
        return [a for a in self.list_accounts() if a.credential_type == credential_type]

    def load_account(self, account_id: str) -> Optional[AccountCredential]:
        """加载指定 account_id 的凭证。"""
        path = self._file_path(account_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return AccountCredential(**data)
        except Exception as e:
            logger.warning("account_store: failed to load %s: %s", account_id, e)
            return None

    # ---- 写入 ----

    def save_account(
        self,
        account_id: str,
        token: str,
        base_url: str,
        user_id: str = "",
    ) -> AccountCredential:
        """保存账号凭证到磁盘。"""
        credential = AccountCredential(
            account_id=account_id,
            token=token,
            base_url=base_url,
            user_id=user_id,
            saved_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        path = self._file_path(account_id)
        path.write_text(
            json.dumps(asdict(credential), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        path.chmod(0o600)  # 敏感数据，仅 owner 可读
        logger.info("account_store: saved %s", account_id)
        return credential

    def save_credential(self, credential: AccountCredential) -> AccountCredential:
        """保存凭证对象到磁盘。"""
        path = self._file_path(credential.account_id)
        path.write_text(
            json.dumps(asdict(credential), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        path.chmod(0o600)
        logger.info(
            "account_store: saved %s (type=%s)",
            credential.account_id,
            credential.credential_type,
        )
        return credential

    def delete_account(self, account_id: str) -> bool:
        """删除指定账号凭证。返回是否实际删除。"""
        path = self._file_path(account_id)
        if path.exists():
            path.unlink()
            logger.info("account_store: deleted %s", account_id)
            return True
        return False

    # ---- 内部 ----

    def _file_path(self, account_id: str) -> Path:
        return self._root / f"{account_id}.json"
