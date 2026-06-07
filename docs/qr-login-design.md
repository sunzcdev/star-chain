# QR 扫码登录方案 — agent-channel WeChat iLink

> 为 agent-channel 项目设计独立的 WeChat iLink 二维码扫码登录功能。
> 基于 Hermes weixin.py `qr_login()`（第 991~1123 行）的参考实现，
> 脱离 Hermes 框架，作为 agent-channel 的原生模块存在。

---

## 1. 需求概述

### 1.1 要什么

让用户能在终端通过微信扫码完成 iLink Bot 登录，获得 bot token，用于后续的 WeChatAdapter 长轮询。

### 1.2 不是什么

- 不是 OAuth2 / SSO 通用登录
- 不像赖 run.py 的启动流程
- 不是 WeChatAdapter 的内部方法——登录是一次性准备工作，适配器是常驻运行时

### 1.3 使用场景

| 场景 | 用户操作 | 期望 |
|------|---------|------|
| 首次使用 | `python -m agent_channel.login` | 终端打印二维码，扫码后保存凭证 |
| 已有凭证 | `python -m agent_channel.login` | 列出已有账号，可选复用或新建 |
| 多账号 | `python -m agent_channel.login --account my_bot_2` | 指定 account_id，跳过交互选择 |
| 纯脚本 | `python -m agent_channel.login --json` | 输出 JSON 到 stdout，不打印交互信息 |

---

## 2. 整体架构

```
┌─────────────────────────────────────────────────────┐
│               python -m agent_channel.login          │
│                                                      │
│  ┌─────────────────┐    ┌────────────────────────┐  │
│  │   QrLoginCLI     │    │    AccountStore         │  │
│  │  - 交互式 CLI     │───▶│  - list_accounts()      │  │
│  │  - QR 渲染       │    │  - save_account()       │  │
│  │  - 状态轮询       │    │  - load_account()       │  │
│  │  - 超时/重试      │    │  - delete_account()     │  │
│  └─────────────────┘    └───────────┬────────────┘  │
│                                      │               │
│  ┌─────────────────┐                ▼               │
│  │   iLink API      │    ┌────────────────────────┐  │
│  │  - get_bot_qrcode│    │  ~/.agent-channel/     │  │
│  │  - get_qrcode_st │    │  weixin/accounts/      │  │
│  │    atus          │    │  ├─ {id1}.json          │  │
│  └─────────────────┘    │  └─ {id2}.json          │  │
│                          └────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

### 数据流（首次登录）

```
1. QrLoginCLI.__init__()
2. AccountStore.list_accounts() → 无记录
3. iLink GET /get_bot_qrcode?bot_type=3
   → {qrcode, qrcode_img_content}
4. 终端渲染二维码（ASCII + URL 链接）
5. 轮询 GET /get_qrcode_status?qrcode={value}
   status=wait    → 继续等待（打印 .）
   status=scaned  → 提示"已扫码，请确认"
   status=confirmed → 获取 {ilink_bot_id, bot_token, baseurl, ilink_user_id}
   status=expired → 刷新二维码（最多 3 次）
6. AccountStore.save_account(account_id, token, base_url, user_id)
7. 输出成功信息
```

### 数据流（已有凭证）

```
1. QrLoginCLI.__init__()
2. AccountStore.list_accounts() → [{id1, ...}, {id2, ...}]
3. 提示用户选择：复用 / 新建 / 删除
4a. 复用 → 直接返回凭证信息
4b. 新建 → 走首次登录流程
```

---

## 3. 模块设计

### 3.1 项目文件结构

```
~/projects/agent-channel/
├── src/agent_channel/
│   ├── __init__.py
│   ├── channel_adapter.py     # 已实现
│   ├── wechat_adapter.py      # 已实现 — iLink 通信层
│   ├── runtime.py              # 已实现
│   ├── session.py              # 已实现
│   ├── utils.py                # 已实现
│   ├── account_store.py        # ★ 新增 — 账号凭证管理
│   └── login.py                # ★ 新增 — 扫码登录入口
└── docs/
    └── qr-login-design.md      # 本文件
```

### 3.2 AccountStore — 账号凭证管理

```python
# src/agent_channel/account_store.py

import json
import logging
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 默认存储根目录
DEFAULT_STORAGE_ROOT = "~/.agent-channel/weixin/accounts"


@dataclass
class AccountCredential:
    """一份 iLink Bot 账号凭证。"""
    account_id: str          # ilink_bot_id
    token: str               # bot_token
    base_url: str            # baseurl (iLink API 地址)
    user_id: str             # ilink_user_id (绑定微信号)
    saved_at: str            # ISO 时间戳


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
```

#### 存储 Schema

每个文件 `{account_id}.json`:

```json
{
  "account_id": "bot_xxxxx",
  "token": "ilink_bot_token_xxxx",
  "base_url": "https://ilinkai.weixin.qq.com",
  "user_id": "wx_user_id_xxx",
  "saved_at": "2026-06-07T10:30:00Z"
}
```

**安全说明：** 文件权限为 `0o600`（仅 owner 读写）。不存储明文密码外的额外敏感信息。如果需要更强的加密（如 SQLCipher），可在后续版本替换存储后端，接口不变。

### 3.3 login.py — 扫码登录入口

```python
# src/agent_channel/login.py

"""
CLI 入口: python -m agent_channel.login [options]

Options:
  --account <id>      指定 account_id（跳过列表选择）
  --json              以 JSON 格式输出结果到 stdout（静默模式）
  --timeout <sec>     二维码超时时间（默认 480s=8min）
  --bot-type <n>      iLink bot type（默认 3）
"""

import argparse
import asyncio
import json as json_mod
import logging
import sys
import time
from typing import Optional

import aiohttp

from .account_store import AccountStore, AccountCredential

logger = logging.getLogger(__name__)

# iLink 常量（与 wechat_adapter.py 保持一致）
ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
EP_GET_BOT_QR = "ilink/bot/get_bot_qrcode"
EP_GET_QR_STATUS = "ilink/bot/get_qrcode_status"
QR_TIMEOUT_MS = 15_000
DEFAULT_TIMEOUT_SECONDS = 480  # 8 分钟
MAX_REFRESH = 3

# 需要 qrcode 库来渲染 ASCII 二维码（可选依赖）
try:
    import qrcode as _qr
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False


# ============================================================
# 核心登录函数
# ============================================================

async def qr_login(
    *,
    account_store: AccountStore,
    bot_type: str = "3",
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    json_mode: bool = False,
) -> Optional[AccountCredential]:
    """执行交互式 iLink QR 扫码登录。

    Args:
        account_store: AccountStore 实例，用于保存凭证。
        bot_type: iLink bot 类型（默认 3）。
        timeout_seconds: 二维码超时时间（秒）。
        json_mode: 如果为 True，不打印交互信息。

    Returns:
        成功返回 AccountCredential，失败返回 None。
    """
    connector = aiohttp.TCPConnector(force_close=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        # ---- 获取二维码 ----
        try:
            qr_resp = await _api_get(
                session,
                endpoint=f"{EP_GET_BOT_QR}?bot_type={bot_type}",
            )
        except Exception as exc:
            _log_or_print("ERROR: 获取二维码失败: %s", exc, json_mode)
            return None

        qrcode_value = str(qr_resp.get("qrcode") or "")
        qrcode_url = str(qr_resp.get("qrcode_img_content") or "")
        if not qrcode_value:
            _log_or_print("ERROR: 二维码响应缺少 qrcode", json_mode=json_mode)
            return None

        # 渲染二维码
        qr_scan_data = qrcode_url if qrcode_url else qrcode_value
        _render_qr(qr_scan_data, qrcode_url, json_mode)

        # ---- 轮询扫码状态 ----
        deadline = time.time() + timeout_seconds
        current_base_url = ILINK_BASE_URL
        refresh_count = 0

        while time.time() < deadline:
            try:
                status_resp = await _api_get(
                    session,
                    base_url=current_base_url,
                    endpoint=f"{EP_GET_QR_STATUS}?qrcode={qrcode_value}",
                )
            except asyncio.TimeoutError:
                await asyncio.sleep(1)
                continue
            except Exception as exc:
                _log_or_print("WARN: 轮询错误: %s", exc, json_mode)
                await asyncio.sleep(1)
                continue

            status = str(status_resp.get("status") or "wait")

            if status == "wait":
                _log_or_print(".", end="", flush=True, json_mode=json_mode)

            elif status == "scaned":
                _log_or_print("\n✓ 已扫码，请在微信中确认登录...", json_mode=json_mode)

            elif status == "scaned_but_redirect":
                redirect_host = str(status_resp.get("redirect_host") or "")
                if redirect_host:
                    current_base_url = f"https://{redirect_host}"

            elif status == "expired":
                refresh_count += 1
                if refresh_count > MAX_REFRESH:
                    _log_or_print("\n✗ 二维码多次过期，请重新运行登录。", json_mode=json_mode)
                    return None
                _log_or_print(
                    "\n二维码已过期，正在刷新… (%d/%d)",
                    refresh_count, MAX_REFRESH, json_mode=json_mode,
                )
                try:
                    new_qr = await _api_get(
                        session,
                        endpoint=f"{EP_GET_BOT_QR}?bot_type={bot_type}",
                    )
                    qrcode_value = str(new_qr.get("qrcode") or "")
                    qrcode_url = str(new_qr.get("qrcode_img_content") or "")
                    qr_scan_data = qrcode_url if qrcode_url else qrcode_value
                    _render_qr(qr_scan_data, qrcode_url, json_mode)
                except Exception as exc:
                    _log_or_print("ERROR: 刷新二维码失败: %s", exc, json_mode)
                    return None

            elif status == "confirmed":
                account_id = str(status_resp.get("ilink_bot_id") or "")
                token = str(status_resp.get("bot_token") or "")
                base_url = str(status_resp.get("baseurl") or ILINK_BASE_URL)
                user_id = str(status_resp.get("ilink_user_id") or "")
                if not account_id or not token:
                    _log_or_print(
                        "ERROR: 扫码成功但凭证不完整", json_mode=json_mode
                    )
                    return None

                credential = account_store.save_account(
                    account_id=account_id,
                    token=token,
                    base_url=base_url,
                    user_id=user_id,
                )
                _log_or_print(
                    "\n✓ 微信连接成功！account_id=%s", account_id,
                    json_mode=json_mode,
                )
                return credential

            await asyncio.sleep(1)

        _log_or_print("\n✗ 微信登录超时。", json_mode=json_mode)
        return None


# ============================================================
# 辅助函数
# ============================================================

async def _api_get(
    session: aiohttp.ClientSession,
    endpoint: str,
    base_url: str = ILINK_BASE_URL,
) -> dict:
    """简化的 iLink GET 调用（复用 wechat_adapter 的 _json_dumps/_headers 逻辑）。"""
    url = f"{base_url.rstrip('/')}/{endpoint}"
    timeout = aiohttp.ClientTimeout(total=QR_TIMEOUT_MS / 1000)
    async with session.get(url, timeout=timeout) as resp:
        if not resp.ok:
            raise RuntimeError(f"iLink GET {endpoint} HTTP {resp.status}")
        return await resp.json()


def _render_qr(
    qr_scan_data: str,
    qrcode_url: str,
    json_mode: bool,
) -> None:
    """在终端渲染二维码（ASCII + URL 链接）。"""
    if json_mode:
        return

    print("\n请使用微信扫描以下二维码：")
    if qrcode_url:
        print(qrcode_url)
    if HAS_QRCODE:
        try:
            qr = _qr.QRCode()
            qr.add_data(qr_scan_data)
            qr.make(fit=True)
            qr.print_ascii(invert=True)
        except Exception as exc:
            print(f"（终端二维码渲染失败: {exc}，请直接打开上面的链接）")


def _log_or_print(msg: str, *args, end: str = "\n", flush: bool = False,
                  json_mode: bool = False) -> None:
    """根据 json_mode 决定是否 print 进度信息。"""
    if json_mode:
        return
    print(msg % args if args else msg, end=end, flush=flush)


# ============================================================
# CLI 入口
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="agent-channel WeChat iLink QR 扫码登录",
    )
    parser.add_argument(
        "--account", "-a",
        help="指定 account_id（跳过列表选择）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出结果到 stdout（静默模式）",
    )
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS,
        help=f"二维码超时秒数（默认 {DEFAULT_TIMEOUT_SECONDS}）",
    )
    parser.add_argument(
        "--bot-type", default="3",
        help="iLink bot type（默认 3）",
    )
    return parser


async def _async_main(args: argparse.Namespace) -> int:
    store = AccountStore()

    # ---- 如果指定了 --account，直接尝试加载已有凭证 ----
    if args.account:
        existing = store.load_account(args.account)
        if existing:
            if args.json:
                print(json_mod.dumps({
                    "status": "reused",
                    "credential": {
                        "account_id": existing.account_id,
                        "token": existing.token,
                        "base_url": existing.base_url,
                        "user_id": existing.user_id,
                        "saved_at": existing.saved_at,
                    },
                }))
            else:
                print(f"\n✓ 已加载现有账号: {existing.account_id}")
            return 0
        else:
            # 指定了 account_id 但不存在 → 直接走登录流程
            pass

    # ---- 检查是否已有账号 ----
    existing_accounts = store.list_accounts()
    if existing_accounts and not args.account:
        if args.json:
            # JSON 模式：列出账号，让调用方决定
            print(json_mod.dumps({
                "status": "existing",
                "accounts": [
                    {
                        "account_id": a.account_id,
                        "user_id": a.user_id,
                        "saved_at": a.saved_at,
                    }
                    for a in existing_accounts
                ],
            }))
            return 0

        # 交互模式：让用户选择
        print("\n已有 %d 个微信账号:" % len(existing_accounts))
        for i, acct in enumerate(existing_accounts, 1):
            print(f"  [{i}] {acct.account_id} (user: {acct.user_id}, saved: {acct.saved_at})")
        print("  [n] 新建账号")
        print("  [d] 删除账号")
        print("  [q] 退出")

        choice = input("\n请选择: ").strip().lower()
        if choice == "q":
            return 0
        if choice == "n":
            pass  # 走到下面的登录流程
        elif choice == "d":
            del_num = input("输入要删除的序号: ").strip()
            try:
                idx = int(del_num) - 1
                if 0 <= idx < len(existing_accounts):
                    store.delete_account(existing_accounts[idx].account_id)
                    print(f"已删除账号 {existing_accounts[idx].account_id}")
                else:
                    print("序号无效")
                return 0
            except ValueError:
                print("输入无效")
                return 1
        else:
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(existing_accounts):
                    acct = existing_accounts[idx]
                    print(f"\n✓ 复用账号: {acct.account_id}")
                    return 0
                else:
                    print("序号无效")
                    return 1
            except ValueError:
                print("输入无效")
                return 1

    # ---- 执行扫码登录 ----
    credential = await qr_login(
        account_store=store,
        bot_type=args.bot_type,
        timeout_seconds=args.timeout,
        json_mode=args.json,
    )

    if credential is None:
        if args.json:
            print(json_mod.dumps({"status": "failed", "reason": "login_failed"}))
        return 1

    if args.json:
        print(json_mod.dumps({
            "status": "success",
            "credential": {
                "account_id": credential.account_id,
                "token": credential.token,
                "base_url": credential.base_url,
                "user_id": credential.user_id,
                "saved_at": credential.saved_at,
            },
        }))
    return 0


def main() -> None:
    """CLI 入口点。"""
    parser = build_parser()
    args = parser.parse_args()
    try:
        exit_code = asyncio.run(_async_main(args))
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n用户中断")
        sys.exit(130)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

---

## 4. 状态流转图

```
                    ┌─────────┐
                    │ 开始     │
                    └────┬────┘
                         │
                         ▼
               ┌─────────────────┐
               │ 列出已有账号      │
               └────────┬────────┘
                        │
           ┌────────────┼────────────┐
           ▼            ▼            ▼
     ┌──────────┐ ┌──────────┐ ┌──────────┐
     │ 复用已有  │ │ 删除已有  │ │ 新建     │
     └────┬─────┘ └────┬─────┘ └────┬─────┘
          │            │            │
          ▼            ▼            ▼
     ┌────────┐  ┌──────────┐  ┌─────────────────┐
     │ 返回    │  │ 删除文件  │  │ 获取二维码       │
     │ 凭证    │  │ 返回      │  │ GET qrcode      │
     └────────┘  └──────────┘  └────────┬────────┘
                                        │
                                        ▼
                                  ┌─────────────────┐
                                  │ 终端渲染二维码    │
                                  │ URL + ASCII QR  │
                                  └────────┬────────┘
                                           │
                                ┌──────────┴──────────┐
                                ▼                     ▼
                          ┌──────────┐         ┌──────────┐
                          │ 轮询状态  │         │ 超时     │
                          │ (1s间隔) │         │ 退出     │
                          └────┬─────┘         └──────────┘
                               │
                  ┌────────────┼────────────────┐
                  ▼            ▼                ▼
            ┌──────────┐ ┌──────────┐    ┌──────────┐
            │ wait     │ │ scaned  │    │ expired  │
            │ 打印 .   │ │ 提示确认 │    │ 刷新二维码│
            └──────────┘ └──────────┘    │ (最多3次)│
                  │            │         └──────────┘
                  │            │              │
                  └─────┬──────┘              │
                        ▼                     │
                  ┌──────────┐                │
                  │ confirmed│◄───────────────┘
                  │ 保存凭证  │
                  │ 返回结果  │
                  └──────────┘
```

### 状态说明

| 状态值 | 含义 | 处理 |
|--------|------|------|
| `wait` | 等待扫码 | 每秒轮询，打印 `.` 表示进度 |
| `scaned` | 已扫码 | 提示用户在微信确认，继续轮询 |
| `scaned_but_redirect` | 已扫码需重定向 | 切换 API base_url |
| `confirmed` | 确认成功 | 提取凭证，保存到 AccountStore |
| `expired` | 二维码过期 | 自动刷新二维码（最多 3 次） |

### 超时策略

- **单次轮询超时：** 15 秒（`QR_TIMEOUT_MS`），超时重试
- **总超时：** 默认 8 分钟（`DEFAULT_TIMEOUT_SECONDS`），可通过 `--timeout` 修改
- **二维码过期重试：** 最多 3 次，彻底超时后返回 None

---

## 5. 与 WeChatAdapter 的集成点

### 5.1 构造时注入凭证

```python
# run.py 中组装流程
from agent_channel.account_store import AccountStore
from agent_channel.wechat_adapter import WeChatAdapter

# 1. 从存储加载凭证（或用 login 模块新建）
store = AccountStore()
credential = store.load_account("bot_xxxxx")

# 2. 注入到 WeChatAdapter
adapter = WeChatAdapter(
    token=credential.token,
    account_id=credential.account_id,
    base_url=credential.base_url,
)

# 3. 启动
```

### 5.2 AccountStore 与 WeChatAdapter 完全解耦

| 组件 | 依赖关系 |
|------|---------|
| `AccountStore` | 零依赖，纯文件读写 |
| `login.py` | 依赖 `AccountStore` + `aiohttp` |
| `WeChatAdapter` | 构造时接收 `token`/`account_id`/`base_url`，不感知存储 |
| `run.py` | 组装以上三者 |

**设计原则：** WeChatAdapter 不关心凭证从哪来。你可以从 AccountStore 加载、从环境变量读取、或硬编码测试——Adapter 只需要原始字符串。

### 5.3 初始化失败场景

```python
# 检查凭证是否存在
credential = store.load_account(account_id)
if credential is None:
    print("ERROR: 账号不存在，请先运行 python -m agent_channel.login")
    sys.exit(1)

# 检查 token 是否为空
if not credential.token:
    print("ERROR: 账号 token 为空，请重新登录")
    sys.exit(1)
```

---

## 6. 多账号场景处理

### 6.1 同一微信号可创建多个 iLink Bot

iLink Bot API 允许一个微信号创建多个 bot 实例，每个 bot 有独立的 `account_id`。Agent-channel 的 AccountStore 按 `{account_id}.json` 存储，天然支持多账号共存。

### 6.2 多账号的路由

iLink 服务端按 account_id 路由消息，所以：

- **一个 WeChatAdapter 实例** = 一个 iLink Bot 账号
- **多个 WeChatAdapter** = 多个 iLink Bot，可以连接到同一个 AgentRuntime 或不同的 Runtime

```python
# 多账号启动示例
adapters = []
for credential in store.list_accounts():
    adapter = WeChatAdapter(
        token=credential.token,
        account_id=credential.account_id,
        base_url=credential.base_url,
    )
    await adapter.start(on_message)
    adapters.append(adapter)
```

### 6.3 CLI 多账号管理

```
$ python -m agent_channel.login

已有 2 个微信账号:
  [1] bot_abc123 (user: wx_user_1, saved: 2026-06-07T10:30:00Z)
  [2] bot_def456 (user: wx_user_2, saved: 2026-06-07T11:00:00Z)
  [n] 新建账号
  [d] 删除账号
  [q] 退出

请选择: 2
✓ 复用账号: bot_def456
```

```
# 无交互模式，指定账号
$ python -m agent_channel.login --account bot_abc123 --json
{"status": "reused", "credential": {"account_id": "bot_abc123", ...}}
```

---

## 7. 与 Hermes 实现的差异

| 维度 | Hermes weixin.py | agent-channel 本方案 |
|------|------------------|---------------------|
| 所属框架 | Hermes BasePlatformAdapter | 独立 CLI 模块 |
| 存储路径 | `{hermes_home}/weixin/accounts/` | `~/.agent-channel/weixin/accounts/` |
| 凭证管理 | `save_weixin_account` 函数 | `AccountStore` 类（CRUD + 列表） |
| 多账号选择 | 无（覆盖写入） | 列表选择 + 复用 + 删除 |
| CLI 参数 | 无（函数级别） | `--account` `--json` `--timeout` |
| QR 码渲染 | 内置 `import qrcode` | 同（可选依赖，支持 fallback） |
| 依赖 | aiohttp + Hermes 内部函数 | 仅 aiohttp（可选 + qrcode） |
| 输出格式 | print 文本 | 文本 + JSON 双模式 |

---

## 8. 依赖

| 依赖 | 用途 | 安装 |
|------|------|------|
| `aiohttp` | iLink API HTTP 调用 | `pip install aiohttp` |
| `qrcode` | 终端 ASCII 二维码渲染（可选） | `pip install qrcode` |

两者都已存在于 agent-channel 项目的 `pyproject.toml` 依赖中（`aiohttp` 是必须的，`qrcode` 是 WeChatAdapter 的间接依赖）。

---

## 9. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| iLink 接口变化 | 无法获取/轮询二维码 | 保持与 Hermes weixin.py 同版本 iLink API |
| 微信扫码权限问题 | 二维码被拦截 | bot_type=3 已验证，无需额外权限 |
| token 泄露 | 他人可用 bot | 文件 0o600 权限；建议用户不要共享账号文件 |
| 网络不稳定 | 轮询失败/超时 | 自动重试（1s 间隔），二维码过期自动刷新 |
| 多账号配置文件冲突 | 误覆盖 | 每个账号独立文件，不会覆盖 |

---

## 10. 验收标准

- [ ] `python -m agent_channel.login` 正常运行，打印二维码链接
- [ ] 二维码 ASCII 渲染正常（安装 qrcode 后）
- [ ] 扫码后轮询状态流转正常（wait → scaned → confirmed）
- [ ] 凭证正确保存至 `~/.agent-channel/weixin/accounts/{id}.json`
- [ ] 已有账号时列出菜单，可选择复用/新建/删除
- [ ] `--account` 参数可直接指定已有账号
- [ ] `--json` 模式输出 JSON 到 stdout，无交互信息
- [ ] 二维码过期后自动刷新（最多 3 次）
- [ ] 超时后优雅退出（默认 8 分钟）
- [ ] 凭证文件权限为 0o600
- [ ] 多账号共存不冲突
- [ ] WeChatAdapter 构造时传入凭证正常运行
