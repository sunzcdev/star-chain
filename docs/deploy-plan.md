# Agent Channel 完整部署方案

> 本文档描述 agent-channel（微信渠道适配层）从零到一的完整部署流程。
> 适用场景：在同一台服务器上、同一个微信号，同时运行 **Hermes** 和 **agent-channel**。

---

## 目录

1. [前置条件](#1-前置条件)
2. [获取第二 iLink Bot 凭证（QR 扫码流程）](#2-获取第二-ilink-bot-凭证qr-扫码流程)
3. [配置与环境变量](#3-配置与环境变量)
4. [启动 agent-channel](#4-启动-agent-channel)
5. [保持持久运行](#5-保持持久运行)
6. [验证部署成功](#6-验证部署成功)
7. [与 Hermes 和平共处](#7-与-hermes-和平共处)
8. [故障排查](#8-故障排查)

---

## 1. 前置条件

| 条件 | 说明 | 验证命令 |
|------|------|----------|
| Python ≥ 3.11 | agent-channel 需要 3.11+ | `python3 --version` |
| 项目已安装 | `pip install -e .` 已完成 | `cd ~/projects/agent-channel && python -c "import agent_channel; print('OK')"` |
| 可选依赖 | 二维码终端渲染需要 | `pip install qrcode[pil]` |
| DeepSeek API Key | 可在 Hermes 环境变量中复用 | `echo $DEEPSEEK_API_KEY` |
| 网络可达 | iLink API → `ilinkai.weixin.qq.com` | `curl -s -o /dev/null -w "%{http_code}" https://ilinkai.weixin.qq.com` |

**执行者：** Executor

```bash
# 验证 Python 版本
python3 --version
# 预期输出: Python 3.11.x 或 3.12.x

# 验证项目安装
cd ~/projects/agent-channel
source .venv/bin/activate
python -c "import agent_channel; print('✅ agent_channel import OK')"
# 预期输出: ✅ agent_channel import OK

# 安装二维码依赖（推荐）
pip install qrcode[pil]
```

---

## 2. 获取第二 iLink Bot 凭证（QR 扫码流程）

### 2.1 原理说明

- Hermes 已绑定第一个 iLink Bot（account_id: `4324c4146a59@im.bot`，凭证在 `~/.hermes/weixin/accounts/`）
- iLink 协议**支持一个微信号绑定多个 Bot**，每个 Bot 有独立的 token 和 account_id
- agent-channel 需要**全新的、独立的 Bot 凭证**，不与 Hermes 共享
- `login.py` 通过 QR 扫码交互式创建新 Bot，凭证自动保存到 `~/.agent-channel/weixin/accounts/`

### 2.2 流程总览

```
Manager(你) ──→ Executor ──→ 执行 login.py ──→ 生成二维码 ──→ Manager 截图/转发给用户
    ↑                                                                   ↓
    └────────── 用户微信扫码确认 ──────── Executor 收到凭证 ──────────────┘
```

### 2.3 详细步骤

#### 步骤 2.3.1：Executor 启动登录（终端模式）

**执行者：** Executor

```bash
cd ~/projects/agent-channel
source .venv/bin/activate
python -m agent_channel.login
```

**预期交互输出：**

```
已有 1 个微信账号:
  [1] 4324c4146a59@im.bot (user: o9cq...@im.wechat, saved: 2026-05-31T15:13:06Z)
  [n] 新建账号
  [d] 删除账号
  [q] 退出

请选择: n
```

> ⚠️ **这是 Hermes 已绑的账号，切勿复用！** 必须选择 `[n]` 新建。

选择 `n` 后出现二维码：

```
请使用微信扫描以下二维码：
█████████████████████████████
████ ▄▄▄▄▄ █▄▀▄█ ▄▄▄▄▄ ████
████ █   █ █▄ ▄█ █   █ ████
...（ASCII 二维码图形）...
████ ▀▀▀▀▀ █▀ ▀█ ▀▀▀▀▀ ████
█████████████████████████████

等待扫码中...
```

#### 步骤 2.3.2：Executor 转发二维码给 Manager

**执行者：** Executor → **Manager**

Executor 有两种方式转发二维码：

**方式 A — 截图分享（推荐）：**
Executor 在终端中对二维码截图或拍照，通过聊天工具发给 Manager。

**方式 B — 复制二维码链接：**
Executor 观察终端输出，若有 `二维码链接: https://...` 字样，复制链接发给 Manager。

**方式 C — JSON 模式（脚本化）：**

```bash
# Executor 用 JSON 模式运行，输出 JSON 给 Manager
cd ~/projects/agent-channel && source .venv/bin/activate
python -m agent_channel.login --json
# 输出示例（第一次运行且无已有账号时）：
# {"status": "wait_qr", "qrcode_url": "https://ilinkai.weixin.qq.com/ilink/bot/qrcode/..."}
```

JSON 模式下的 `qrcode_url` 可以直接粘贴到浏览器（或微信内打开）。

#### 步骤 2.3.3：用户用微信扫码

**执行者：** 用户

1. Manager 将二维码图片/链接发给用户
2. 用户打开微信 → 扫一扫 → 扫描二维码
3. 微信中弹出「xxx 请求绑定 iLink Bot」
4. 用户点击 **确认**

#### 步骤 2.3.4：Executor 侧自动完成

**执行者：** Executor（自动化）

扫码确认后，Executor 终端自动显示：

```
✓ 已扫码，请在微信中确认登录...
✓ 微信连接成功！account_id=c57c83055340@im.bot
```

凭证已自动保存到 `~/.agent-channel/weixin/accounts/c57c83055340@im.bot.json`

**验证凭证文件：**

```bash
cat ~/.agent-channel/weixin/accounts/*.json
```

**预期输出（示例，字段值可能不同）：**

```json
{
  "account_id": "c57c83055340@im.bot",
  "token": "c57c83055340@im.bot:0600002cdfe963f5b546f4b98d078e535ed8f7",
  "base_url": "https://ilinkai.weixin.qq.com",
  "user_id": "o9cq808wkwkocfYWYyWnQZQiAKY0@im.wechat",
  "saved_at": "2026-05-31T15:08:10Z"
}
```

---

### 2.4 备用方案：复用已有 Bot

如果已有一个未使用的 Bot 凭证（例如之前 Hermes 注册过第二个 Bot），可以直接复制到 agent-channel 的存储目录：

**执行者：** Executor

```bash
# 查看 Hermes 下已有的 Bot 账号
ls ~/.hermes/weixin/accounts/*.json

# 假设有第二个 bot c57c83055340@im.bot，复制到 agent-channel
mkdir -p ~/.agent-channel/weixin/accounts
cp ~/.hermes/weixin/accounts/c57c83055340@im.bot.json ~/.agent-channel/weixin/accounts/
```

> ⚠️ 注意：确保复制的是**非 Hermes 正在使用的** Bot。Hermes 的 active bot 是 `4324c4146a59@im.bot`。

---

## 3. 配置与环境变量

### 3.1 环境变量一览

agent-channel 支持两种配置方式（优先级：环境变量 > AccountStore > 默认值）：

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `DEEPSEEK_API_KEY` | ✅ | — | DeepSeek API Key（可从 Hermes 环境复用） |
| `DEEPSEEK_BASE_URL` | ❌ | `https://api.deepseek.com/v1` | API 地址 |
| `DEEPSEEK_MODEL` | ❌ | `deepseek-ai/DeepSeek-V3` | 模型名称 |
| `WEIXIN_TOKEN` | ⚠️ | AccountStore | iLink Bot Token（不设则从 AccountStore 加载） |
| `WEIXIN_ACCOUNT_ID` | ⚠️ | AccountStore | iLink Bot 账号 ID（不设则从 AccountStore 加载） |
| `WEIXIN_BASE_URL` | ❌ | `https://ilinkai.weixin.qq.com` | iLink API 地址 |
| `AGENT_CHANNEL_SESSION_DIR` | ❌ | `~/.agent-channel/sessions` | 会话存储目录 |
| `AGENT_CHANNEL_MAX_TURNS` | ❌ | `30` | Agent 最大对话轮次 |
| `AGENT_CHANNEL_LOG_LEVEL` | ❌ | `INFO` | 日志级别 |

### 3.2 设置环境变量

**执行者：** Executor

```bash
# 方式一：直接 export（适合手动启动）
export DEEPSEEK_API_KEY="sk-your-key-here"

# 如果 DEEPSEEK_API_KEY 已在 Hermes 环境中，自动复用：
# echo $DEEPSEEK_API_KEY

# 可选：修改模型
export DEEPSEEK_MODEL="deepseek-ai/DeepSeek-V3"

# 方式二：写入 ~/.bashrc（持久化）
cat >> ~/.bashrc << 'EOF'

# agent-channel 配置
export DEEPSEEK_API_KEY="sk-your-key-here"
export DEEPSEEK_BASE_URL="https://api.deepseek.com/v1"
export DEEPSEEK_MODEL="deepseek-ai/DeepSeek-V3"
EOF

source ~/.bashrc
```

> 💡 **重点：** 如果 Hermes 已经设置了 `DEEPSEEK_API_KEY` 环境变量，agent-channel 会自动复用——无需重复设置。
>
> 验证 Hermes 的 API Key 是否可用：
> ```bash
> echo "DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY:0:8}..."
> ```

### 3.3 关于 WEIXIN_TOKEN / WEIXIN_ACCOUNT_ID

这两个变量**不需要**手动设置。`run.py` 中的 `_get_config()` 启动时会：

1. 优先从 `~/.agent-channel/weixin/accounts/` 下加载第一个可用凭证
2. 如果 AccountStore 为空，回退到环境变量 `WEIXIN_TOKEN` / `WEIXIN_ACCOUNT_ID`

只要 QR 登录流程成功执行过，凭证就自动在 AccountStore 中可用。

---

## 4. 启动 agent-channel

### 4.1 验证凭证可访问

**执行者：** Executor

```bash
cd ~/projects/agent-channel
source .venv/bin/activate

# 查看 AccountStore 中是否有凭证
python -c "
from agent_channel.account_store import AccountStore
store = AccountStore()
accounts = store.list_accounts()
print(f'找到 {len(accounts)} 个账号:')
for a in accounts:
    print(f'  - {a.account_id} (user: {a.user_id[:20]}...)')
"
```

**预期输出：**

```
找到 1 个账号:
  - c57c83055340@im.bot (user: o9cq808wkwkocfYWYyWnQZQiAKY0@im.wechat...)
```

### 4.2 前台启动（开发/调试）

**执行者：** Executor

```bash
cd ~/projects/agent-channel
source .venv/bin/activate
python run.py
```

**预期启动日志：**

```
2026-06-07 12:00:00 [INFO] agent_channel.utils: Logging configured
2026-06-07 12:00:00 [INFO] agent_channel.runtime: AgentRuntime initialized (model=deepseek-ai/DeepSeek-V3, base=https://api.deepseek.com/v1, max_turns=30)
2026-06-07 12:00:00 [INFO] agent_channel.account_store: loaded weixin credentials from AccountStore: c57c83055340@im.bot
2026-06-07 12:00:00 [INFO] agent_channel.wechat_adapter: WeChatAdapter started (account=c57c83055340@im.bot, base=https://ilinkai.weixin.qq.com)
2026-06-07 12:00:00 [INFO] __main__: Agent Channel started — listening via iLink long-poll (account=c57c83055340@im.bot)
```

### 4.3 启动后验证

在另一个终端中检查进程和日志：

**执行者：** Manager / Executor

```bash
# 检查进程是否存在
ps aux | grep "python run.py" | grep -v grep

# 查看日志（实时 tail）
tail -f ~/.agent-channel/logs/agent-channel.log

# 检查网络连接（长轮询连接）
ss -tnp | grep ilinkai
```

---

## 5. 保持持久运行

### 5.1 方案对比

| 方案 | 复杂度 | 自动重启 | 日志管理 | 推荐场景 |
|------|--------|----------|----------|----------|
| `nohup` | ★☆☆ | ❌ | ❌ | 临时、调试 |
| `tmux/screen` | ★☆☆ | ❌ | ❌ | 开发调试 |
| `systemd` | ★★★ | ✅ | ✅ | **生产推荐** |
| Supervisor | ★★☆ | ✅ | ✅ | 备选 |

### 5.2 方案一：systemd service（推荐）

**执行者：** Executor

创建 service 文件：

```bash
sudo tee /etc/systemd/system/agent-channel.service << 'SERVICE'
[Unit]
Description=Agent Channel — WeChat iLink Bot AI Gateway
Documentation=https://github.com/nousresearch/agent-channel
After=network.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/projects/agent-channel

# DeepSeek API（可复用 Hermes 的环境变量）
Environment=DEEPSEEK_API_KEY=sk-your-key-here
Environment=DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
Environment=DEEPSEEK_MODEL=deepseek-ai/DeepSeek-V3

# Runtime
Environment=AGENT_CHANNEL_LOG_LEVEL=INFO
Environment=AGENT_CHANNEL_SESSION_DIR=~/.agent-channel/sessions
Environment=AGENT_CHANNEL_MAX_TURNS=30

ExecStart=/home/ubuntu/projects/agent-channel/.venv/bin/python /home/ubuntu/projects/agent-channel/run.py

# 日志
StandardOutput=journal
StandardError=journal

# 重启策略
Restart=on-failure
RestartSec=10
StartLimitIntervalSec=60
StartLimitBurst=3

# 安全（可选）
NoNewPrivileges=true
ProtectSystem=full
PrivateTmp=true

[Install]
WantedBy=multi-user.target
SERVICE
```

> ⚠️ 将 `DEEPSEEK_API_KEY=sk-your-key-here` 替换为实际的 API Key。

启用并启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable agent-channel.service
sudo systemctl start agent-channel.service
```

检查状态：

```bash
sudo systemctl status agent-channel.service
```

**预期输出：**

```
● agent-channel.service - Agent Channel — WeChat iLink Bot AI Gateway
     Loaded: loaded (/etc/systemd/system/agent-channel.service; enabled; vendor preset: enabled)
     Active: active (running) since Sun 2026-06-07 12:00:00 CST; 5s ago
   Main PID: 12345 (python)
      Tasks: 2 (limit: 2345)
     Memory: 45.2M
        CPU: 1.2s
     CGroup: /system.slice/agent-channel.service
             └─12345 /home/ubuntu/projects/agent-channel/.venv/bin/python /home/ubuntu/projects/agent-channel/run.py
```

### 5.3 方案二：nohup（临时）

**执行者：** Executor

```bash
cd ~/projects/agent-channel
source .venv/bin/activate

nohup python run.py > ~/.agent-channel/nohup.log 2>&1 &
echo $! > ~/.agent-channel/pid.txt
echo "PID: $(cat ~/.agent-channel/pid.txt)"
```

停止：

```bash
kill $(cat ~/.agent-channel/pid.txt)
```

### 5.4 方案三：tmux 会话

**执行者：** Executor

```bash
# 创建 tmux 会话
tmux new-session -d -s agent-channel

# 在会话中启动
tmux send-keys -t agent-channel "cd ~/projects/agent-channel && source .venv/bin/activate && python run.py" Enter

# 查看日志
tmux attach -t agent-channel

# 分离（不停止进程）：Ctrl+B, D
```

### 5.5 日志管理

agent-channel 内置了日志轮转：

- **主日志：** `~/.agent-channel/logs/agent-channel.log`（每日轮转，保留 7 天）
- **错误日志：** `~/.agent-channel/logs/errors.log`（WARNING 及以上，保留 30 天）
- **systemd journal：** 如果使用 systemd，同时记录到 `journalctl`

查看日志：

```bash
# 实时查看
tail -f ~/.agent-channel/logs/agent-channel.log

# 查看最后 50 行
tail -50 ~/.agent-channel/logs/agent-channel.log

# 查看今天全部日志
cat ~/.agent-channel/logs/agent-channel.log

# systemd 日志
sudo journalctl -u agent-channel.service -n 100 -f
```

---

## 6. 验证部署成功

### 6.1 服务状态检查

**执行者：** Manager / Executor

```bash
# systemd 启动的
sudo systemctl is-active agent-channel.service
# 预期输出: active

# 进程检查
ps aux | grep "run.py" | grep -v grep
# 预期输出: ubuntu 12345 ... python run.py

# 端口/连接检查（iLink 长轮询使用 HTTPS 出站连接，没有监听端口）
ss -tnp | grep ilinkai.weixin.qq.com
# 预期输出: 有 ESTAB 连接
```

### 6.2 日志验证

**执行者：** Executor

```bash
tail -20 ~/.agent-channel/logs/agent-channel.log
```

**预期日志内容（核心验证点）：**

```
[INFO] agent_channel.runtime: AgentRuntime initialized (...)        ← Runtime 初始化成功
[INFO] agent_channel.account_store: loaded weixin credentials ...  ← 凭证加载成功
[INFO] agent_channel.wechat_adapter: WeChatAdapter started (...)   ← 适配器启动成功
[INFO] __main__: Agent Channel started — listening ...              ← 主循环开始
```

### 6.3 端到端验证

**执行者：** 用户（通过微信）

**场景：** 用户向 agent-channel Bot 发送一条消息，确认收到回复。

1. **用户在微信中打开与 agent-channel Bot 的对话**
   - 注意：这个 Bot 与 Hermes 的 Bot 是**不同的对话窗口**
   - 如果同一个微信号绑了两个 Bot，微信里会有两个不同的聊天入口

2. **用户发送：** `你好`

3. **预期：** 5~10 秒内收到回复（DeepSeek 响应时间 + 网络延迟）

4. **验证日志：**
   ```bash
   tail -5 ~/.agent-channel/logs/agent-channel.log
   ```
   **预期输出：**
   ```
   [INFO] __main__: received message from o9cq...@im.wechat: 你好...
   [INFO] agent_channel.wechat_adapter: send_message to o9cq...@im.wechat returned errcode=0
   [INFO] __main__: sent response to o9cq...@im.wechat: 你好！有什么可以帮你的？...
   ```

5. **验证特殊命令 `/new`：**
   - 用户发送 `/new`
   - 预期回复：`会话已重置。有什么可以帮你的？`

6. **验证 `/stop` / `/quit` 优雅关闭（可选）：**
   - 用户发送 `/stop`
   - 服务优雅停止，进程退出
   - 需用 systemd 重启

### 6.4 测试套件验证

**执行者：** Executor

```bash
cd ~/projects/agent-channel
source .venv/bin/activate
python -m pytest tests/ -v
```

**预期输出：**

```
============================= test session starts ==============================
tests/test_core.py::test_session_context PASSED
tests/test_core.py::test_session_history_cap PASSED
tests/test_core.py::test_runtime_import PASSED
tests/test_core.py::test_wechat_adapter_import PASSED
tests/test_integration.py::test_wechat_adapter_process_message PASSED
tests/test_integration.py::test_wechat_adapter_send_message PASSED
tests/test_integration.py::test_wechat_adapter_send_message_failure PASSED
tests/test_integration.py::test_wechat_adapter_stop PASSED
tests/test_integration.py::test_wechat_adapter_start_stop (expected timeout)
tests/test_integration.py::test_wechat_adapter_poll_loop_updates_sync_buf PASSED

============================= 9 passed, 1 timeout in X.XXs =====================
```

> 已知 `test_wechat_adapter_start_stop` 超时（mock I/O 不阻塞导致），不影响生产运行。

### 6.5 检查清单

| 检查项 | 命令/方法 | 通过标准 |
|--------|-----------|----------|
| 进程运行 | `ps aux \| grep run.py` | 进程存在 |
| 日志无 ERROR | `grep ERROR ~/.agent-channel/logs/agent-channel.log` | 无意外 ERROR |
| 日志有启动标记 | `grep "started" ~/.agent-channel/logs/agent-channel.log` | 有 WeChatAdapter started |
| 凭证已加载 | `grep "loaded weixin" ~/.agent-channel/logs/agent-channel.log` | 有凭证加载记录 |
| 微信消息可达 | 用户发送消息 | 5~10 秒内收到回复 |
| systemd 已启用（如使用） | `systemctl is-enabled agent-channel.service` | enabled |
| systemd 自动重启 | `sudo systemctl kill -s KILL agent-channel.service` | 10 秒后自动重启 |

---

## 7. 与 Hermes 和平共处

### 7.1 架构隔离

```
同一个微信号
    ├── iLink Bot #1 (Hermes 使用)
    │   ├── account_id: 4324c4146a59@im.bot
    │   ├── token: 保存在 ~/.hermes/weixin/accounts/
    │   └── 聊天入口: 微信中「Hermes Bot」对话
    │
    ├── iLink Bot #2 (agent-channel 使用)
    │   ├── account_id: c57c83055340@im.bot （示例，实际值不同）
    │   ├── token: 保存在 ~/.agent-channel/weixin/accounts/
    │   └── 聊天入口: 微信中「Agent Channel Bot」对话
    │
    └── iLink 协议层：两个 Bot 完全独立，互不干扰
```

### 7.2 关键隔离点

| 维度 | Hermes | agent-channel | 冲突风险 |
|------|--------|---------------|----------|
| **凭证存储** | `~/.hermes/weixin/accounts/` | `~/.agent-channel/weixin/accounts/` | ✅ 完全隔离 |
| **消息轮询** | Hermes gateway 进程 | agent-channel run.py 进程 | ✅ 各自长轮询自己的 Bot |
| **会话存储** | `~/.hermes/sessions/` | `~/.agent-channel/sessions/` | ✅ 完全隔离 |
| **日志** | `~/.hermes/logs/` | `~/.agent-channel/logs/` | ✅ 完全隔离 |
| **AI API Key** | `DEEPSEEK_API_KEY` | `DEEPSEEK_API_KEY` | ⚠️ 可以共用 |
| **模型调用** | Hermes 自己的 agent 流水线 | OpenAI Agents SDK 流水线 | ✅ 各自独立调用 |
| **端口** | 无（出站长轮询） | 无（出站长轮询） | ✅ 无端口冲突 |
| **systemd 服务名** | `hermes.service` | `agent-channel.service` | ✅ 不同服务名 |

### 7.3 资源竞争检查

**CPU/内存：** 两个进程各自运行，agent-channel 空闲时约 30-50MB 内存，DeepSeek API 调用时短暂 CPU 峰值。与 Hermes 共同运行在一台 4C8G 服务器上完全无压力。

**网络：** 各自独立长轮询 iLink API，互不阻塞。iLink 长轮询 35 秒超时 + 2 秒重试间隔，两个进程各自维护一个 HTTPS 连接。

### 7.4 共存的注意事项

1. **不要复用 Hermes 的 Bot Token**
   - ❌ 错误：`cp ~/.hermes/weixin/accounts/4324c4146a59@im.bot.json ~/.agent-channel/weixin/accounts/`
   - ✅ 正确：通过 `python -m agent_channel.login` 新建独立的 Bot

2. **两个 Bot 在微信中是不同的对话窗口**
   - 用户需要识别哪个对话是 Hermes，哪个是 agent-channel
   - 建议给两个 Bot 分别设置不同的昵称和头像（通过 iLink 后台）

3. **`/stop` 命令只影响 agent-channel**
   - 在 agent-channel 的对话窗口中发 `/stop` 只停止 agent-channel
   - Hermes 的 Bot 对话中发 `/stop` 不影响 agent-channel

4. **日志文件分开，互不干扰**

### 7.5 快速诊断：确认使用的是哪个 Bot

```bash
# 查看 agent-channel 使用的 Bot
grep "account=" ~/.agent-channel/logs/agent-channel.log | tail -1

# 查看 Hermes 使用的 Bot
grep -r "account_id" ~/.hermes/weixin/accounts/*.json | head -3

# 确认不是同一个（account_id 应不同）
echo "Hermes account: $(ls ~/.hermes/weixin/accounts/*.json | head -1 | xargs basename)"
echo "agent-channel account: $(ls ~/.agent-channel/weixin/accounts/*.json | head -1 | xargs basename)"
```

---

## 8. 故障排查

### 8.1 启动失败

| 症状 | 可能原因 | 解决 |
|------|----------|------|
| `FATAL: DEEPSEEK_API_KEY environment variable is required` | 未设置 API Key | `export DEEPSEEK_API_KEY=sk-xxx` |
| `loaded weixin credentials from AccountStore` 未出现 | AccountStore 空 | 运行 `python -m agent_channel.login` 或设置 `WEIXIN_TOKEN` |
| `ModuleNotFoundError: No module named 'agent_channel'` | venv 未激活 | `source .venv/bin/activate` |
| `aiohttp.client_exceptions.ClientConnectorError` | 网络不通 | `curl https://ilinkai.weixin.qq.com` 检查 |

### 8.2 运行时异常

| 日志内容 | 含义 | 处理 |
|----------|------|------|
| `poll_loop error: ...` | 长轮询异常 | 检查网络、Bot token 是否过期（token 长期有效，极少过期） |
| `send_message failed to ...` | 发送失败 | 检查 iLink API 可用性 |
| `handle_message error for ...` | AI 模型调用失败 | 检查 DeepSeek API Key 是否有效、余额是否充足 |
| `errcode=...` 非零 | iLink API 业务错误 | 查阅 iLink 错误码文档 |

### 8.3 微信消息不通

1. 确认 agent-channel 进程在运行
2. 确认用户给**正确的 Bot** 发消息（不是 Hermes 的 Bot）
3. 查看日志是否有 `received message from ...` 行
4. 如果没有 → 检查长轮询是否正常工作（`_get_updates` 是否返回数据）
5. 如果有收到但没回复 → 查看 handler 是否有错误日志

### 8.4 Token 过期 / 需要重新绑定

如遇到 Bot 无法连接（持续 `poll_loop error`），可能需要重新绑定：

```bash
# 1. 停掉 agent-channel
sudo systemctl stop agent-channel.service

# 2. 删除旧凭证
rm ~/.agent-channel/weixin/accounts/*.json

# 3. 重新登录
cd ~/projects/agent-channel && source .venv/bin/activate
python -m agent_channel.login

# 4. 重启
sudo systemctl start agent-channel.service
```

---

## 附录 A：快速部署速查表

```bash
# ===== Executor 执行 =====

# 1. 安装依赖
cd ~/projects/agent-channel
source .venv/bin/activate
pip install qrcode[pil]

# 2. 创建第二 Bot（选择 [n] 新建）
python -m agent_channel.login

# 3. 设置 API Key（如果 Hermes 没有设环境变量）
export DEEPSEEK_API_KEY="sk-your-key-here"

# 4. 测试前台启动
python run.py
# 预期: 看到 "Agent Channel started — listening" 日志

# 5. 设置 systemd 服务
sudo tee /etc/systemd/system/agent-channel.service << 'EOF'
[Unit]
Description=Agent Channel
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/projects/agent-channel
Environment=DEEPSEEK_API_KEY=sk-your-key-here
Environment=DEEPSEEK_MODEL=deepseek-ai/DeepSeek-V3
ExecStart=/home/ubuntu/projects/agent-channel/.venv/bin/python /home/ubuntu/projects/agent-channel/run.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now agent-channel.service

# 6. 验证
sudo systemctl status agent-channel.service
tail -f ~/.agent-channel/logs/agent-channel.log
```

---

## 附录 B：角色职责速查

| 步骤 | 执行者 | 动作 |
|------|--------|------|
| 环境准备 | Executor | 安装依赖、验证 Python 版本 |
| 获取 Bot 凭证 | Executor → Manager → 用户 | 运行 login.py，转发二维码，用户扫码 |
| 配置环境变量 | Executor | 设置 DEEPSEEK_API_KEY |
| 前台启动验证 | Executor | `python run.py`，观察启动日志 |
| 设置持久运行 | Executor | 配置 systemd service |
| 验证部署 | Manager / Executor | 检查进程、日志、发送测试消息 |
| 诊断问题 | Manager / Executor | 查看日志、检查进程、检查网络 |
| 确认共存 | Manager | 确认两个 Bot 互不冲突 |
