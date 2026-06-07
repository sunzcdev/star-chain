# 飞书 QR 扫码绑定方案设计

> agent-channel — 飞书应用一键创建 + 凭证持久化
> 纯方案文档，不涉及具体实现代码；伪代码仅供参考接口设计。

---

## 1. 背景与现状

### 已完成
- **FeishuAdapter** — 已在 `feishu_adapter.py` 实现，支持 WebSocket 模式
- **run.py 切换** — 已支持 `ADAPTER_TYPE=feishu` 环境变量
- **WeChat QR 登录** — `login.py` 已有完整的 `qr_login()` + `_render_qr()` + `AccountStore` 流程

### 当前痛点
使用飞书通道需要用户在飞书开放平台**手动创建应用**并获取 App ID / App Secret，然后设置环境变量 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`。流程繁琐，不适合新手。

### 目标
用户执行 `feishu_login` CLI → 扫描二维码 → 自动创建飞书应用 → 保存凭证 → run.py 自动加载

---

## 2. 整体流程

```
用户执行 feishu_login.py
  │
  ├─ 1. 调用 lark_oapi.aregister_app(on_qr_code=callback)
  │      │
  │      ├─ 回调: on_qr_code(info)
  │      │   └─ info.url → 渲染二维码到终端（复用 _render_qr）
  │      │
  │      ├─ 用户扫码 + 在飞书 App 确认
  │      │
  │      ├─ 回调: on_status_change(info)
  │      │   └─ status = polling / scanned / domain_switched
  │      │
  │      └─ 返回: {client_id, client_secret, user_info}
  │
  ├─ 2. 保存凭证到 FeishuStore
  │      └─ ~/.agent-channel/feishu/accounts/{app_id}.json
  │
  └─ 3. 输出结果（交互模式 / --json 模式）
```

### run.py 启动流程（改造后）

```
run.py (ADAPTER_TYPE=feishu)
  │
  ├─ 1. 尝试从 FeishuStore 加载已保存的凭证
  │      └─ 成功 → 使用保存的 app_id + app_secret
  │
  ├─ 2. 回退到环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET
  │      └─ 存在 → 使用环境变量
  │
  ├─ 3. 两者都无 → 提示用户先执行 feishu_login
  │
  └─ 4. 创建 FeishuAdapter(app_id, app_secret)
```

---

## 3. AccountStore 适配方案

### 决策：统一 AccountStore vs 独立 FeishuStore

**选择：统一 AccountStore，新增 `credential_type` 区分。**

理由：
- 代码复用度高（list/load/save/delete 逻辑完全一致）
- 单一导入入口，run.py 加载路径一致
- 向后兼容：已有 WeChat 凭证不受影响（credential_type 缺省值 == "wechat"）

### 设计方案

```python
# account_store.py 改动

from dataclasses import dataclass, asdict
from enum import Enum
from typing import Optional

class CredentialType(str, Enum):
    WECHAT = "wechat"
    FEISHU = "feishu"

@dataclass
class AccountCredential:
    """统一凭证模型——所有字段均可选，按 credential_type 解释。"""
    account_id: str              # WeChat: ilink_bot_id / Feishu: app_id
    credential_type: str = "wechat"  # "wechat" | "feishu"

    # WeChat 字段
    token: str = ""              # bot_token
    base_url: str = ""           # iLink API 地址

    # Feishu 字段
    app_secret: str = ""         # app_secret
    domain: str = "feishu.cn"    # feishu.cn / larksuite.com
    user_name: str = ""          # 扫码用户的飞书昵称
    avatar_url: str = ""         # 扫码用户的头像 URL

    # 通用字段
    user_id: str = ""            # WeChat: ilink_user_id / Feishu: open_id
    saved_at: str = ""
```

### 存储路径

| 凭证类型 | 存储根目录 | 文件名 |
|----------|-----------|--------|
| wechat | `~/.agent-channel/weixin/accounts/` | `{account_id}.json` |
| feishu | `~/.agent-channel/feishu/accounts/` | `{app_id}.json` |

### AccountStore 接口扩展

```python
class AccountStore:
    def __init__(self, storage_root: str | None = None):
        # storage_root 不传时从 CredentialType 推导
        pass

    # 现有接口不变（默认 wechat）
    def list_accounts(self) -> list[AccountCredential]: ...
    def load_account(self, account_id: str) -> Optional[AccountCredential]: ...
    def save_account(self, account_id, token, base_url, ...) -> AccountCredential: ...
    def delete_account(self, account_id: str) -> bool: ...

    # 新增：按类型操作
    def list_by_type(self, credential_type: str) -> list[AccountCredential]: ...
    def load_by_type(self, account_id: str, credential_type: str) -> ...: ...
    def save_credential(self, credential: AccountCredential) -> AccountCredential: ...
```

### 向后兼容

- `AccountStore()` 默认构造 → `~/.agent-channel/weixin/accounts/`（同现有）
- 已有 `.json` 文件读取时，缺失 `credential_type` 字段默认为 `"wechat"`
- `save_account()` 现有调用不变（新建的凭证自动带 credential_type="wechat"）

---

## 4. Feishu 登录 CLI (`feishu_login.py`)

### 4.1 整体流程

```python
async def feishu_login(
    *,
    store: AccountStore,
    timeout_seconds: int = 480,
    json_mode: bool = False,
    domain: str = "feishu.cn",
) -> Optional[AccountCredential]:
    """执行飞书 QR 扫码注册应用。

    流程：
    1. 调用 lark_oapi.aregister_app(on_qr_code=handle_qr)
    2. 收到 QR → 渲染到终端（复用 _render_qr）
    3. 等待用户扫码 + 确认
    4. 返回 {client_id, client_secret, user_info}
    5. 保存到 FeishuStore
    """
```

### 4.2 状态反馈

| 阶段 | 交互模式渲染 | JSON 模式输出 |
|------|-------------|---------------|
| 等待扫码 | 二维码 + "请使用飞书扫描以上二维码" | 静默 |
| 已扫码 | "✓ 已扫码，请在手机端确认..." | 静默 |
| 用户拒绝 | "✗ 用户取消了授权" | `{"error": "access_denied"}` |
| 创建成功 | "✓ 飞书应用创建成功！app_id=cli_xxx" | `{"status": "success", "credential": {...}}` |
| 超时 | "✗ 登录超时，请重新运行" | `{"error": "timeout"}` |

### 4.3 `aregister_app` 回调设计

```python
import lark_oapi as lark

async def feishu_login(...) -> Optional[AccountCredential]:
    """基于 lark_oapi.aregister_app 的飞书登录。"""

    qr_info_holder = {}  # 用于异步回调间传值

    def on_qr_code(info: dict):
        """QR 码就绪回调。"""
        qr_info_holder["url"] = info["url"]
        qr_info_holder["expire_in"] = info["expire_in"]
        # 渲染二维码（复用 login.py 的 _render_qr）
        _render_qr(info["url"], qrcode_url=info["url"], json_mode=json_mode)
        _log_or_print(
            "\n请使用飞书扫描以上二维码", json_mode=json_mode
        )

    def on_status_change(info: dict):
        """轮询状态变化回调。"""
        status = info.get("status", "")
        if status == "polling":
            _log_or_print(".", end="", flush=True, json_mode=json_mode)
        elif status == "slow_down":
            _log_or_print(
                " (调整轮询间隔: %ss)", info.get("interval", ""),
                json_mode=json_mode,
            )

    try:
        result = await asyncio.wait_for(
            lark.aregister_app(
                on_qr_code=on_qr_code,
                on_status_change=on_status_change,
                domain=domain,
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

    # 注册成功
    app_id = result["client_id"]
    app_secret = result["client_secret"]
    user_info = result.get("user_info", {})

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
```

### 4.4 CLI 入口

```python
# feishu_login.py

def build_parser() -> argparse.ArgumentParser:
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
        default=480,
        help="二维码超时秒数（默认 480）",
    )
    parser.add_argument(
        "--domain",
        default="feishu.cn",
        choices=["feishu.cn", "larksuite.com"],
        help="飞书域名（默认 feishu.cn，国际版用 larksuite.com）",
    )
    return parser
```

---

## 5. run.py 加载改造

### 5.1 当前逻辑（WeChat 模式）

```
ADAPTER_TYPE=wechat（默认）
  ├─ AccountStore().list_accounts()
  │   └─ 有凭证 → 取第一个
  └─ 无凭证 → 回退到 WEIXIN_TOKEN / WEIXIN_ACCOUNT_ID 环境变量
```

### 5.2 改造后逻辑（Feishu 模式）

```
ADAPTER_TYPE=feishu
  ├─ 1. AccountStore(storage_root="~/.agent-channel/feishu/accounts").list_accounts()
  │      └─ 有凭证 → 取第一个（app_id + app_secret）
  │
  ├─ 2. 回退到 FEISHU_APP_ID / FEISHU_APP_SECRET 环境变量
  │
  └─ 3. 两者都无 → logger.warning("飞书凭证未配置，请先执行 feishu_login")
```

### 5.3 伪代码

```python
# run.py _get_config() 改造

def _get_config() -> dict:
    adapter_type = os.environ.get("ADAPTER_TYPE", "wechat").lower()
    config = {"adapter_type": adapter_type}

    if adapter_type == "feishu":
        config.update(_get_feishu_config())
    else:
        config.update(_get_wechat_config())
    return config

def _get_feishu_config() -> dict:
    # 1. 尝试从 FeishuStore 加载
    store = AccountStore("~/.agent-channel/feishu/accounts")
    accounts = store.list_accounts()
    if accounts:
        cred = accounts[0]
        logger.info("loaded feishu credentials from AccountStore: %s", cred.account_id)
        return {
            "feishu_app_id": cred.account_id,
            "feishu_app_secret": cred.app_secret,
            "feishu_domain": cred.domain,
        }

    # 2. 回退到环境变量
    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    if app_id and app_secret:
        return {"feishu_app_id": app_id, "feishu_app_secret": app_secret}

    # 3. 无凭证
    logger.warning("飞书凭证未配置，请先执行 `python -m feishu_login` 扫码登录")
    return {"feishu_app_id": "", "feishu_app_secret": ""}
```

---

## 6. 错误处理与边界情况

| 场景 | 处理方式 |
|------|----------|
| **QR 超时** | `asyncio.wait_for` 抛出 TimeoutError → 提示用户重跑 |
| **用户拒绝授权** | `AppAccessDeniedError` → 提示用户需要在飞书 App 确认 |
| **二维码过期** | `AppExpiredError` → 提示重新运行 CLI |
| **网络错误** | 通用 Exception catch → 提示检查网络 |
| **已存在同应用凭证** | `feishu_login` 默认追加（多账号场景）；设计 `--replace` 参数覆盖 |
| **多飞书账号** | FeishuStore 支持多账号（按 app_id 区分），run.py 取第一个 |
| **domain 配置错误** | 飞书国内版用 `feishu.cn`，国际版用 `larksuite.com`，默认国内 |
| **已登录但 app_id 失效** | 飞书侧应用被删除 → FeishuAdapter 连接时报错 → 提示重新登录 |

### 6.1 --replace 参数设计

```python
parser.add_argument(
    "--replace",
    action="store_true",
    help="如已有同名 app_id 凭证，覆盖保存",
)
```

在保存前逻辑：
- 不传 `--replace`：已存在则 skip，输出已存在提示
- 传 `--replace`：覆盖已有凭证

---

## 7. 文件变更清单

| 文件 | 变更类型 | 预估行数 | 预估迭代 | 前置依赖 |
|------|----------|----------|----------|----------|
| `src/agent_channel/account_store.py` | **修改** — 扩展 CredentialType 支持 | ~50 行 | 8-12 轮 | 无 |
| `src/agent_channel/feishu_login.py` | **新增** — 飞书 QR 登录 CLI | ~200 行 | 15-20 轮 | T-1 |
| `run.py` | **修改** — _get_feishu_config() 加载逻辑 | ~30 行 | 5-8 轮 | T-1 |
| `src/agent_channel/__init__.py` | **修改** — 导出 feishu_login | +2 行 | 1 轮 | T-2 |
| **合计** | | **~280 行** | **~35 轮** | |

---

## 8. 任务分解（Executor 可执行）

### 依赖图

```
T-1 (AccountStore 扩展)
  └─→ T-2 (FeishuLogin CLI 核心)
        ├─→ T-3 (run.py 加载改造)
        └─→ T-4 (导入导出 + 测试)
```

T-3 和 T-4 可并行（均依赖 T-2）。

---

### T-1：AccountStore 扩展

**目的**：扩展 AccountStore 支持多种凭证类型

- 涉及文件：`src/agent_channel/account_store.py`
- 前置依赖：无
- 预估：~50 行 / 8-12 轮

**变更详情**：
1. 新增 `CredentialType` 枚举（wechat / feishu）
2. 在 `AccountCredential` dataclass 中新增：`credential_type`, `app_secret`, `domain`, `user_name`, `avatar_url` 字段（均为可选，默认空）
3. 新增 `save_credential(credential: AccountCredential)` 方法
4. 新增 `list_by_type(credential_type: str)` 方法
5. `DEFAULT_STORAGE_ROOT` 改为从 credential_type 动态推导
6. 已有 `.json` 文件缺失 `credential_type` 字段时默认 `"wechat"`

**验收标准**：
- [ ] 已有 WeChat 凭证可正常读取（向后兼容）
- [ ] 新创建的 WeChat 凭证带 credential_type="wechat"
- [ ] 可创建和读取 Feishu 凭证
- [ ] `list_by_type()` 正确过滤
- [ ] `python -c "from src.agent_channel.account_store import AccountCredential, AccountStore, CredentialType"` 无报错

---

### T-2：FeishuLogin CLI

**目的**：实现 `feishu_login.py` — 基于 `lark_oapi.aregister_app` 的 QR 扫码绑定 CLI

- 涉及文件：`src/agent_channel/feishu_login.py`（新建）
- 前置依赖：T-1
- 预估：~200 行 / 15-20 轮

**变更详情**：
1. 创建 `feishu_login.py`，包含：
   - `feishu_login()` 异步函数（调用 `lark_oapi.aregister_app`）
   - QR 码回调 → 复用 `login.py` 的 `_render_qr()`
   - 状态回调 → 复用 `_log_or_print()`
   - 异常处理（超时、拒绝、网络错误）
   - 凭证保存到 AccountStore（`credential_type="feishu"`）
2. `build_parser()` — `--json`, `--timeout`, `--domain`, `--replace`
3. `main()` — async CLI 入口（复用 login.py 的 main 模式）
4. 复用 `login.py` 的 `_log_or_print`, `_render_qr`（通过 from .login import ...）

**验收标准**：
- [ ] `python -m src.agent_channel.feishu_login --help` 输出正确帮助信息
- [ ] `lark_oapi` 未安装时导入给出友好提示
- [ ] QR 码回调正确渲染到终端
- [ ] 登录成功时凭证写入正确路径
- [ ] `--json` 模式不输出交互信息，仅输出 JSON 行

---

### T-3：run.py Feishu 加载改造

**目的**：在 feishu 模式下优先从 AccountStore 加载凭证，回退环境变量

- 涉及文件：`run.py`
- 前置依赖：T-1
- 预估：~30 行 / 5-8 轮

**变更详情**：
1. 新增 `_get_feishu_config()` 函数
2. 修改 `_get_config()` 中的 feishu 分支
3. 引导用户执行 `feishu_login` 的提示

**验收标准**：
- [ ] `ADAPTER_TYPE=feishu` 且 FeishuStore 有凭证时自动加载
- [ ] `ADAPTER_TYPE=feishu` 且无存储凭证时回退到环境变量
- [ ] `ADAPTER_TYPE=wechat` 行为完全不变（回归）

---

### T-4：测试与文档

**目的**：为 FeishuLogin 编写 Mock 测试，更新导出

- 涉及文件：`src/agent_channel/__init__.py`, `tests/test_feishu_login.py`（新建）
- 前置依赖：T-2
- 预估：~100 行 / 8-12 轮

**变更详情**：
1. `__init__.py` — 添加 `from .feishu_login import feishu_login, main as feishu_login_main`
2. `tests/test_feishu_login.py` — Mock 测试：
   - Mock `lark_oapi.aregister_app` 返回 `{client_id, client_secret}`
   - 验证 QR 回调被触发
   - 验证凭证被正确保存到 AccountStore
   - 验证超时/拒绝/异常的路径

**验收标准**：
- [ ] `python -m pytest tests/test_feishu_login.py -v` 全部通过
- [ ] `python -m pytest tests/ -v` 全部（新旧）测试通过
- [ ] `from src.agent_channel import feishu_login, feishu_login_main` 无报错

---

## 9. 技术风险与缓解

| 风险 | 级别 | 说明 | 缓解方案 |
|------|------|------|----------|
| `lark_oapi.aregister_app` 在特定 Python 版本兼容性 | **低** | 需 Python 3.7+ | 项目已用 Python 3.11+, 无问题 |
| `aregister_app` 内部 async 模式与现有 event loop 冲突 | **低** | 是标准的 asyncio coroutine | 直接 await，与 login.py 一致 |
| `on_qr_code` 回调与 QR 渲染的线程问题 | **低** | `aregister_app` 在内部线程调用回调 | 回调只是设置变量 + 打印，无线程安全问题 |
| AccountStore 已有文件兼容 | **低** | 旧文件缺 `credential_type` 字段 | 读取时缺省为 "wechat" |
| Feishu API 限频 | **低** | SDK 内部已处理 | 无需额外处理 |

---

## 10. 验收总标准

- [ ] 方案文档已写入 `~/projects/agent-channel/plans/feishu-login-plan.md`
- [ ] AccountStore 扩展向后兼容已有 WeChat 凭证
- [ ] feishu_login CLI 可完成一次完整的 QR 扫码绑定流程
- [ ] run.py 在 feishu 模式下优先加载已保存凭证
- [ ] 4 个子任务可独立执行，依赖关系清晰
- [ ] Mock 测试覆盖主要成功/失败路径
