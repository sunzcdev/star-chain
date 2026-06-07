# QR 扫码登录 — 实现任务拆解清单

> 基于 `docs/qr-login-design.md`（已完成的设计方案），分解为可独立执行的实现任务。
> 目标：Executor 能在 20 次迭代内完成每个任务。

---

## 总览

```
T-1 (AccountStore) ──▶ T-2 (login.py 辅助层) ──▶ T-3 (qr_login 核心) ──▶ T-4 (CLI 入口)
       │                                                                    │
       └──────────────────── 复用给 WeChatAdapter ──────────────────────────┘
```

| 任务 | 标题 | 依赖 | 预估迭代 | 涉及文件 |
|------|------|------|---------|---------|
| T-1 | AccountStore 实现 | 无 | ~2 | `account_store.py` |
| T-2 | login.py 辅助函数 & 常量 | 无 | ~1 | `login.py` |
| T-3 | qr_login 核心登录函数 | T-1, T-2 | ~4 | `login.py` |
| T-4 | CLI 入口 & 多账号管理 | T-1, T-3 | ~3 | `login.py` |

---

## T-1: AccountStore — 账号凭证存储层

**依赖：** 无（零依赖纯 Python 文件操作）

**目标文件：** `src/agent_channel/account_store.py`

**实现内容：**

1. `AccountCredential` dataclass
   - 字段：account_id, token, base_url, user_id, saved_at
   - dataclass 装饰器 + asdict 支持

2. `AccountStore` class
   - `__init__(storage_root)` — 确保目录存在
   - `list_accounts()` — 扫描 `*.json`，容错加载
   - `load_account(account_id)` — 单文件加载
   - `save_account(account_id, token, base_url, user_id)` — 写入 + chmod 0o600
   - `delete_account(account_id)` — 删除文件
   - `_file_path(account_id)` — 内部辅助

3. 常量 `DEFAULT_STORAGE_ROOT = "~/.agent-channel/weixin/accounts"`

**验收标准：**
- [ ] `AccountCredential(**data)` 能从 dict 还原
- [ ] `AccountStore` 创建后目录自动创建
- [ ] `save_account()` 写入 JSON 文件，权限为 0o600
- [ ] `list_accounts()` 返回所有已保存账号
- [ ] `load_account()` 返回指定账号，不存在返回 None
- [ ] `delete_account()` 删除指定文件，返回 bool
- [ ] 损坏的 JSON 文件被 `list_accounts()` 跳过（log warning）
- [ ] import 不报错（无外部依赖，仅 json/time/logging/dataclass/pathlib）

**涉及文件：**
- `src/agent_channel/account_store.py`（新建）
- 验证：手动创建/读/写/删若干 JSON 文件

---

## T-2: login.py — 辅助函数 & 常量

**依赖：** 无（可独立实现，不依赖其他新文件）

**目标文件：** `src/agent_channel/login.py`（部分 — 常量 + 辅助函数）

**实现内容：**

1. 常量定义
   - `ILINK_BASE_URL`, `EP_GET_BOT_QR`, `EP_GET_QR_STATUS`
   - `QR_TIMEOUT_MS = 15_000`, `DEFAULT_TIMEOUT_SECONDS = 480`
   - `MAX_REFRESH = 3`

2. 可选依赖检测
   - `try: import qrcode; HAS_QRCODE = True` + 容错

3. 函数 `_api_get(session, endpoint, base_url)` — async
   - 构造完整 URL，设 timeout，GET 请求
   - 非 200 抛 RuntimeError
   - 返回 JSON dict

4. 函数 `_render_qr(qr_scan_data, qrcode_url, json_mode)` — sync
   - json_mode 时直接 return
   - 打印 "请使用微信扫描以下二维码："
   - 有 URL 则打印 URL
   - 有 `qrcode` 库则渲染 ASCII 二维码
   - 渲染失败时打印 fallback 提示

5. 函数 `_log_or_print(msg, *args, end, flush, json_mode)` — sync
   - json_mode 时 return（不打印）
   - 否则 `print(msg % args, end=end, flush=flush)`

**验收标准：**
- [ ] 常量值正确（与 wechat_adapter.py 保持一致）
- [ ] `HAS_QRCODE` 反映真实安装状态
- [ ] `_api_get` 调用可构造正确 URL 并发送 GET
- [ ] `_render_qr` 在终端打印二维码链接
- [ ] `_log_or_print` 在 json_mode 下静默
- [ ] Python 语法无错误，可 import

**涉及文件：**
- `src/agent_channel/login.py`（新建 — 写入常量 + 辅助函数，留空核心函数位置）

---

## T-3: qr_login — 核心登录函数

**依赖：** T-1（AccountStore 用于保存凭证）、T-2（辅助函数）

**目标文件：** `src/agent_channel/login.py`（追加 qr_login 函数）

**实现内容：**

1. `async def qr_login(account_store, bot_type, timeout_seconds, json_mode)`

2. 获取二维码
   - 创建 `aiohttp.ClientSession`
   - 调用 `_api_get` 获取二维码
   - 解析 `qrcode` 和 `qrcode_img_content`
   - 调用 `_render_qr`

3. 轮询状态循环 (1s 间隔)
   - `wait` → 打印 `.`
   - `scaned` → 打印"已扫码，请确认"
   - `scaned_but_redirect` → 更新 `current_base_url`
   - `confirmed` → 提取 credential（ilink_bot_id, bot_token, baseurl, ilink_user_id），保存至 AccountStore，返回 credential
   - `expired` → 刷新二维码（最多 3 次），超过则返回 None

4. 超时处理
   - deadline = time.time() + timeout_seconds
   - 超时返回 None

5. 错误处理
   - 网络错误 → await asyncio.sleep(1) 继续
   - 凭据不完整 → 返回 None
   - 获取二维码失败 → 返回 None

**验收标准：**
- [ ] 二维码获取逻辑正确（调用 `_api_get` 传参正确）
- [ ] 5 种状态处理分支齐全（wait/scaned/scaned_but_redirect/confirmed/expired）
- [ ] 二维码过期后自动刷新（最多 3 次）
- [ ] 确认后调用 `account_store.save_account()` 保存凭证
- [ ] 超时后返回 None
- [ ] 异步函数结构正确（await 非阻塞）
- [ ] 所有 print 输出通过 `_log_or_print`，受 json_mode 控制

**涉及文件：**
- `src/agent_channel/login.py`（追加 qr_login 函数在辅助函数下方）

---

## T-4: CLI 入口 & 多账号管理

**依赖：** T-1（AccountStore CRUD）、T-3（qr_login）
**前置：** T-2（辅助函数已在 login.py 中）

**目标文件：** `src/agent_channel/login.py`（追加 CLI 入口 + 交互菜单）

**实现内容：**

1. `build_parser()` — argparse 参数解析
   - `--account / -a` — 指定 account_id，跳过列表
   - `--json` — JSON 输出模式
   - `--timeout` — 超时秒数（默认 480）
   - `--bot-type` — bot type（默认 3）

2. `async def _async_main(args)` — 主流程调度
   - 创建 AccountStore 实例
   - `--account` 指定：尝试加载已有凭证 → 存在则输出并返回 0
   - 无 `--account`：`list_accounts()` 有记录 → 走交互菜单
   - 无账号 → 直接走 qr_login

3. 交互菜单
   - 显示账号列表（序号、account_id、user_id、saved_at）
   - 选项：[n] 新建、[d] 删除、[q] 退出
   - 序号选择：复用已有账号
   - 删除：输入序号后调用 `delete_account()`
   - 所有输入校验（ValueError 处理）

4. JSON 模式输出
   - 复用已有：`{"status": "reused", "credential": {...}}`
   - 列出账号：`{"status": "existing", "accounts": [...]}`
   - 成功登录：`{"status": "success", "credential": {...}}`
   - 失败：`{"status": "failed", "reason": "login_failed"}`

5. `main()` 入口
   - 解析参数，`asyncio.run(_async_main(args))`
   - `KeyboardInterrupt` → 打印"用户中断"，exit 130
   - 其他异常 → print 到 stderr，exit 1

6. `if __name__ == "__main__": main()`

**验收标准：**
- [ ] `python -m agent_channel.login` 正常运行（无账号时走 QR 流程）
- [ ] 已有账号时显示交互菜单，选择后复用/新建/删除均正常工作
- [ ] `--account bot_xxx` 直接加载已有账号
- [ ] `--json` 模式所有输出为 JSON，无交互信息
- [ ] `--timeout` 自定义超时生效
- [ ] `--bot-type` 自定义 bot type 生效
- [ ] 多账号共存不冲突
- [ ] JSON 模式失败时输出 `{"status": "failed", "reason": "..."}`
- [ ] Ctrl+C 优雅退出（exit 130）
- [ ] 入口文件可被 `python -m` 调用

**涉及文件：**
- `src/agent_channel/login.py`（追加 CLI 入口代码）
- `docs/qr-login-design.md`（参考，不改）

---

## 执行顺序建议

```
T-1 ──▶ T-2 ──▶ T-3 ──▶ T-4
              (T-1, T-2 可并行)
```

1. **T-1** 先做 — AccountStore 是基础设施，零依赖，独立可测
2. **T-2** 可与 T-1 并行编写 — 辅助函数不依赖 AccountStore
3. **T-3** 等 T-1 + T-2 就绪 — 核心 login 逻辑需要 AccountStore 和辅助函数
4. **T-4** 最后 — CLI 入口需要完整的 login 流程

---

## 风险提醒

| 风险 | 影响 | 缓解 |
|------|------|------|
| login.py 文件合并冲突 | T-2/T-3/T-4 写同一文件 | 按顺序串行执行，前一个任务完成后下一个再开始 |
| iLink API 实际不可用 | qr_login 无法端到端测试 | T-3 验收以代码结构和类型正确为准，不要求真实 API 调用 |
| aiohttp 版本兼容 | 异步 HTTP 调用异常 | 使用项目已有的 aiohttp 版本（与 wechat_adapter 一致） |
