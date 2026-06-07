"""
登录流程 — 二维码获取、状态轮询、辅助函数。

本文件包含 iLink Bot 登录所需的常量和辅助函数。
核心登录函数由 T-3 实现，此处仅预留位置。
"""

import json
import logging
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)

# ---- 常量定义 ----

ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"

# 二维码 API 端点
EP_GET_BOT_QR = "ilink/bot/get_bot_qrcode"
EP_GET_QR_STATUS = "ilink/bot/get_qrcode_status"

# 超时 & 重试
QR_TIMEOUT_MS = 15_000          # 单次二维码操作超时
DEFAULT_TIMEOUT_SECONDS = 480   # 登录总超时（8 分钟）
MAX_REFRESH = 3                 # 二维码最大刷新次数

# ---- 可选依赖检测 ----

try:
    import qrcode  # noqa: F811

    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False


# ---- 辅助函数 ----

async def _api_get(
    session: aiohttp.ClientSession,
    endpoint: str,
    base_url: str = ILINK_BASE_URL,
) -> dict:
    """向 iLink API 发送 GET 请求并返回 JSON 响应。

    Args:
        session: aiohttp 会话。
        endpoint: API 路径（如 ``ilink/bot/get_bot_qrcode``）。
        base_url: API 基础地址。

    Returns:
        解析后的 JSON dict。

    Raises:
        RuntimeError: 非 200 状态码时抛出。
    """
    url = f"{base_url.rstrip('/')}/{endpoint}"
    timeout = aiohttp.ClientTimeout(total=QR_TIMEOUT_MS / 1000)
    async with session.get(url, timeout=timeout) as response:
        raw = await response.text()
        if not response.ok:
            raise RuntimeError(
                f"iLink GET {endpoint} HTTP {response.status}: {raw[:200]}"
            )
        return json.loads(raw)


def _render_qr(
    qr_scan_data: str,
    qrcode_url: Optional[str] = None,
    json_mode: bool = False,
) -> None:
    """在终端渲染登录二维码。

    Args:
        qr_scan_data: 二维码扫描数据（用于生成 ASCII 二维码）。
        qrcode_url: 可选的二维码图片链接（打印给用户）。
        json_mode: JSON 模式时静默返回。
    """
    if json_mode:
        return

    print("请使用微信扫描以下二维码：")

    if qrcode_url:
        print(f"二维码链接: {qrcode_url}")

    if HAS_QRCODE and qr_scan_data:
        try:
            qr = qrcode.QRCode()
            qr.add_data(qr_scan_data)
            qr.print_ascii(invert=True)
        except Exception:
            print("(二维码渲染失败，请手动打开以上链接)")
    elif qr_scan_data:
        print("(缺少 qrcode 库，请安装: pip install qrcode[pil])")


def _log_or_print(
    msg: str,
    *args: Any,
    end: str = "\n",
    flush: bool = False,
    json_mode: bool = False,
) -> None:
    """输出日志或打印消息。

    在 JSON 模式下静默；否则调用 ``print``。

    Args:
        msg: 消息文本（支持 ``%`` 格式化）。
        *args: 格式化参数。
        end: print 的 end 参数。
        flush: print 的 flush 参数。
        json_mode: JSON 模式时静默。
    """
    if json_mode:
        return
    if args:
        msg = msg % args
    print(msg, end=end, flush=flush)


# ===== 核心登录函数（T-3 实现）=====

import argparse
import asyncio
import sys
import time as _time

from .account_store import AccountCredential, AccountStore


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
            _log_or_print("ERROR: 获取二维码失败: %s", exc, json_mode=json_mode)
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
        deadline = _time.time() + timeout_seconds
        current_base_url = ILINK_BASE_URL
        refresh_count = 0

        while _time.time() < deadline:
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
                _log_or_print("WARN: 轮询错误: %s", exc, json_mode=json_mode)
                await asyncio.sleep(1)
                continue

            status = str(status_resp.get("status") or "wait")

            if status == "wait":
                _log_or_print(".", end="", flush=True, json_mode=json_mode)

            elif status == "scaned":
                _log_or_print(
                    "\n\xe2\x9c\x93 已扫码，请在微信中确认登录...",
                    json_mode=json_mode,
                )

            elif status == "scaned_but_redirect":
                redirect_host = str(status_resp.get("redirect_host") or "")
                if redirect_host:
                    current_base_url = f"https://{redirect_host}"

            elif status == "expired":
                refresh_count += 1
                if refresh_count > MAX_REFRESH:
                    _log_or_print(
                        "\n\xe2\x9c\x97 二维码多次过期，请重新运行登录。",
                        json_mode=json_mode,
                    )
                    return None
                _log_or_print(
                    "\n二维码已过期，正在刷新\xe2\x80\xa6 (%d/%d)",
                    refresh_count,
                    MAX_REFRESH,
                    json_mode=json_mode,
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
                    _log_or_print(
                        "ERROR: 刷新二维码失败: %s", exc, json_mode=json_mode
                    )
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
                    "\n\xe2\x9c\x93 微信连接成功！account_id=%s",
                    account_id,
                    json_mode=json_mode,
                )
                return credential

            await asyncio.sleep(1)

        _log_or_print("\n\xe2\x9c\x97 微信登录超时。", json_mode=json_mode)
        return None


# ============================================================
# CLI 入口
# ============================================================


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        description="agent-channel WeChat iLink QR 扫码登录",
    )
    parser.add_argument(
        "--account",
        "-a",
        help="指定 account_id（跳过列表选择）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出结果到 stdout（静默模式）",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"二维码超时秒数（默认 {DEFAULT_TIMEOUT_SECONDS}）",
    )
    parser.add_argument(
        "--bot-type",
        default="3",
        help="iLink bot type（默认 3）",
    )
    return parser


async def _async_main(args: argparse.Namespace) -> int:
    """CLI 主异步逻辑。"""
    store = AccountStore()

    # ---- 如果指定了 --account，直接尝试加载已有凭证 ----
    if args.account:
        existing = store.load_account(args.account)
        if existing:
            if args.json:
                print(
                    json.dumps(
                        {
                            "status": "reused",
                            "credential": {
                                "account_id": existing.account_id,
                                "token": existing.token,
                                "base_url": existing.base_url,
                                "user_id": existing.user_id,
                                "saved_at": existing.saved_at,
                            },
                        }
                    )
                )
            else:
                print(f"\n\xe2\x9c\x93 已加载现有账号: {existing.account_id}")
            return 0
        # 指定了 account_id 但不存在 → 继续走登录流程

    # ---- 检查是否已有账号 ----
    existing_accounts = store.list_accounts()
    if existing_accounts and not args.account:
        if args.json:
            # JSON 模式：列出账号，让调用方决定
            print(
                json.dumps(
                    {
                        "status": "existing",
                        "accounts": [
                            {
                                "account_id": a.account_id,
                                "user_id": a.user_id,
                                "saved_at": a.saved_at,
                            }
                            for a in existing_accounts
                        ],
                    }
                )
            )
            return 0

        # 交互模式：让用户选择
        print(f"\n已有 {len(existing_accounts)} 个微信账号:")
        for i, acct in enumerate(existing_accounts, 1):
            print(
                f"  [{i}] {acct.account_id} "
                f"(user: {acct.user_id}, saved: {acct.saved_at})"
            )
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
                    print(f"\n\xe2\x9c\x93 复用账号: {acct.account_id}")
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
            print(json.dumps({"error": "login_failed"}))
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    "status": "success",
                    "credential": {
                        "account_id": credential.account_id,
                        "token": credential.token,
                        "base_url": credential.base_url,
                        "user_id": credential.user_id,
                        "saved_at": credential.saved_at,
                    },
                }
            )
        )
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
