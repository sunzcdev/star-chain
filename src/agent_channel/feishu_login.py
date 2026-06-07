"""飞书 QR 扫码登录 CLI — 基于 lark_oapi.aregister_app 创建飞书应用。

流程：
1. 调用 lark_oapi.aregister_app(on_qr_code=callback)
2. 二维码渲染到终端（复用 login.py 的 _render_qr）
3. 用户扫码 + 在飞书 App 确认
4. 保存凭证到 AccountStore（credential_type="feishu"）
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ---- 可选依赖检测 ----

try:
    import lark_oapi as lark

    HAS_LARK = True
except ImportError:
    HAS_LARK = False
    lark = None  # type: ignore[assignment]


from .account_store import AccountCredential, AccountStore
from .login import _log_or_print, _render_qr


# ---- 常量 ----

FEISHU_STORAGE_ROOT = "~/.agent-channel/feishu/accounts"
DEFAULT_TIMEOUT_SECONDS = 480


# ---- 辅助函数 ----


def _resolve_lark_domain(short_domain: str) -> tuple[str, str]:
    """将短域名映射为 aregister_app 所需的 domain 和 lark_domain。

    Returns:
        (domain, lark_domain) — 完整的 API 基 URL。
    """
    if short_domain == "larksuite.com":
        return "https://accounts.larksuite.com", "https://accounts.larksuite.com"
    return "https://accounts.feishu.cn", "https://accounts.larksuite.com"


async def feishu_login(
    *,
    store: AccountStore,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    json_mode: bool = False,
    domain: str = "feishu.cn",
    replace: bool = False,
) -> Optional[AccountCredential]:
    """执行飞书 QR 扫码注册应用。

    Args:
        store: AccountStore 实例（storage_root 已指向 feishu 目录）。
        timeout_seconds: 登录超时秒数。
        json_mode: JSON 静默模式。
        domain: 飞书域名（feishu.cn / larksuite.com）。
        replace: 是否覆盖已有同名凭证。

    Returns:
        成功返回 AccountCredential，失败返回 None。
    """
    if not HAS_LARK:
        _log_or_print(
            "ERROR: lark-oapi 未安装，请先执行: pip install lark-oapi",
            json_mode=json_mode,
        )
        return None

    def on_qr_code(info: dict) -> None:
        """QR 码就绪回调。"""
        _render_qr(info["url"], qrcode_url=info["url"], json_mode=json_mode)

    def on_status_change(info: dict) -> None:
        """轮询状态变化回调。"""
        status = info.get("status", "")
        if status == "polling":
            _log_or_print(".", end="", flush=True, json_mode=json_mode)
        elif status == "slow_down":
            _log_or_print(
                " (调整轮询间隔: %ss)",
                info.get("interval", ""),
                json_mode=json_mode,
            )
        elif status == "domain_switched":
            _log_or_print(
                "\n检测到国际版飞书账号，自动切换域名...",
                json_mode=json_mode,
            )

    api_domain, api_lark_domain = _resolve_lark_domain(domain)
    _log_or_print("正在获取飞书二维码...", json_mode=json_mode)

    try:
        result = await asyncio.wait_for(
            lark.aregister_app(
                on_qr_code=on_qr_code,
                on_status_change=on_status_change,
                domain=api_domain,
                lark_domain=api_lark_domain,
            ),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        _log_or_print("\n✗ 飞书登录超时。", json_mode=json_mode)
        return None
    except lark.AppAccessDeniedError:
        _log_or_print("\n✗ 用户取消了授权。", json_mode=json_mode)
        return None
    except lark.AppExpiredError:
        _log_or_print("\n✗ 二维码已过期。", json_mode=json_mode)
        return None
    except Exception as e:
        _log_or_print("\n✗ 飞书登录失败: %s", e, json_mode=json_mode)
        return None

    # 注册成功 → 提取结果
    app_id = result["client_id"]
    app_secret = result["client_secret"]
    user_info = result.get("user_info", {})

    # --replace 检查：已存在则跳过
    if not replace:
        existing = store.load_account(app_id)
        if existing:
            _log_or_print(
                "\n- 应用 %s 已存在（使用 --replace 覆盖）",
                app_id,
                json_mode=json_mode,
            )
            return existing

    # 保存凭证
    credential = store.save_credential(
        AccountCredential(
            account_id=app_id,
            credential_type="feishu",
            app_secret=app_secret,
            domain=domain,
            user_name=user_info.get("name", ""),
            user_id=user_info.get("open_id", ""),
            saved_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
    )
    _log_or_print(
        "\n✓ 飞书应用创建成功！app_id=%s", app_id, json_mode=json_mode
    )
    return credential


# ============================================================
# CLI 入口
# ============================================================


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        description="agent-channel 飞书 QR 扫码登录 — 一键创建飞书应用",
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
        "--domain",
        default="feishu.cn",
        choices=["feishu.cn", "larksuite.com"],
        help="飞书域名（默认 feishu.cn，国际版用 larksuite.com）",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="如已有同名 app_id 凭证，覆盖保存",
    )
    return parser


async def _async_main(args: argparse.Namespace) -> int:
    """CLI 主异步逻辑。"""
    store = AccountStore(storage_root=FEISHU_STORAGE_ROOT)

    # 执行扫码登录
    credential = await feishu_login(
        store=store,
        timeout_seconds=args.timeout,
        json_mode=args.json,
        domain=args.domain,
        replace=args.replace,
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
                        "credential_type": credential.credential_type,
                        "app_secret": credential.app_secret,
                        "domain": credential.domain,
                        "user_name": credential.user_name,
                        "user_id": credential.user_id,
                        "saved_at": credential.saved_at,
                    },
                }
            )
        )
    return 0


def main() -> None:
    """CLI 入口点。"""
    if not HAS_LARK:
        print(
            "ERROR: lark-oapi 未安装，请先执行: pip install lark-oapi",
            file=sys.stderr,
        )
        sys.exit(1)

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
