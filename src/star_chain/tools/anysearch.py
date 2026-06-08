"""工具层 — AnySearch 统一实时搜索服务。

基于 AnySearch JSON-RPC 2.0 API (https://api.anysearch.com/mcp)，
提供 4 个工具：
  - any_search:        通用 / 垂直领域搜索
  - any_search_domains: 查询垂直领域目录
  - any_search_batch:  批量并行搜索（2-5 个查询）
  - any_search_extract: 提取网页全文内容

API Key 读取优先级：
  环境变量 ANYSEARCH_API_KEY > 项目根目录 .env >
  ~/.hermes/profiles/*/skills/anysearch/.env > 匿名访问

匿名访问可用但有速率限制，建议配置 API Key。
"""

import json
import os
from pathlib import Path
from typing import Optional

import httpx
from agents import function_tool

# ── 常量 ──────────────────────────────────────────────────────────────

ANYSEARCH_ENDPOINT = "https://api.anysearch.com/mcp"
ANYSEARCH_TIMEOUT = 30  # 秒

AVAILABLE_DOMAINS = [
    "code", "tech", "fashion", "travel", "home", "ecommerce",
    "gaming", "film", "music", "finance", "academic", "legal",
    "business", "ip", "security", "education", "health", "religion",
    "geo", "environment", "energy", "ugc",
]

CONTENT_TYPES = [
    "web", "news", "code", "doc", "academic",
    "data", "image", "video", "audio",
]


# ── API Key 加载 ──────────────────────────────────────────────────────


def _load_api_key() -> str:
    """按优先级加载 ANYSEARCH_API_KEY。"""
    key = os.environ.get("ANYSEARCH_API_KEY", "").strip()
    if key:
        return key

    # 1. 项目根目录 .env
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).parent.parent.parent / ".env",
    ]
    # 2. Hermes skill 目录
    hermes_root = Path.home() / ".hermes"
    if hermes_root.exists():
        for profile_dir in hermes_root.glob("profiles/*/skills/anysearch"):
            candidates.append(profile_dir / ".env")
        candidates.append(hermes_root / "skills" / "anysearch" / ".env")

    for env_path in candidates:
        if env_path.is_file():
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    if k.strip() == "ANYSEARCH_API_KEY":
                        key = v.strip().strip('"').strip("'")
                        if key:
                            return key
            except Exception:
                continue

    return ""


# ── API 调用 ──────────────────────────────────────────────────────────


def _call_anysearch(tool_name: str, arguments: dict) -> str:
    """调用 AnySearch JSON-RPC 2.0 API。"""
    api_key = _load_api_key()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }

    try:
        with httpx.Client(timeout=ANYSEARCH_TIMEOUT) as client:
            resp = client.post(ANYSEARCH_ENDPOINT, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        return f"AnySearch 请求超时（{ANYSEARCH_TIMEOUT}s）"
    except httpx.ConnectError as e:
        return f"AnySearch 网络不可达：{e}"
    except httpx.HTTPStatusError as e:
        detail = f"HTTP {e.response.status_code}"
        try:
            err = e.response.json()
            if "error" in err:
                detail += f": {err['error'].get('message', str(err['error']))}"
        except Exception:
            body = e.response.text[:300]
            if body:
                detail += f" - {body}"
        return f"AnySearch HTTP 错误：{detail}"
    except Exception as e:
        return f"AnySearch 调用失败：{type(e).__name__}: {e}"

    if "error" in data:
        error_msg = data["error"].get("message", str(data["error"]))
        return f"AnySearch API 错误：{error_msg}"

    result = data.get("result", {})
    content = result.get("content", [])
    for item in content:
        if item.get("type") == "text":
            return item.get("text", "")
    return json.dumps(result, indent=2, ensure_ascii=False)


# ── 工具函数 ──────────────────────────────────────────────────────────


def any_search(
    query: str,
    domain: Optional[str] = None,
    sub_domain: Optional[str] = None,
    content_types: Optional[str] = None,
    zone: Optional[str] = None,
    max_results: int = 10,
    freshness: Optional[str] = None,
) -> str:
    """AnySearch 实时搜索（通用或垂直领域）。

    优先于 web_search 使用，支持 23 个垂直领域、内容类型过滤、
    时间范围筛选等高级功能。

    Args:
        query: 搜索关键词。垂直搜索时遵循 list_domains 返回的 query_format
        domain: 可选，垂直领域：code/tech/finance/academic/legal/security/health 等
        sub_domain: 垂直搜索必须，具体子领域路由键（如 finance.us_stock）
        content_types: 可选，内容类型过滤：web/news/code/doc/academic/data/image/video/audio（逗号分隔）
        zone: 可选，区域：cn（中国）/ intl（国际）
        max_results: 返回结果数（1-100，默认 10）
        freshness: 可选，时间过滤：day/week/month/year

    Returns:
        格式化的搜索结果（标题 + URL + 摘要）。
    """
    arguments: dict = {"query": query}

    if domain:
        if domain not in AVAILABLE_DOMAINS:
            return f"无效 domain：{domain}。可用：{', '.join(AVAILABLE_DOMAINS)}"
        arguments["domain"] = domain
        if sub_domain:
            arguments["sub_domain"] = sub_domain
        else:
            return (
                f"垂直搜索需要 sub_domain 参数。\n"
                f"请先调用 any_search_domains(domain='{domain}') 查询可用子领域。"
            )

    if content_types:
        if "," in content_types:
            arguments["content_types"] = [t.strip() for t in content_types.split(",") if t.strip()]
        else:
            ct = content_types.strip()
            if ct:
                arguments["content_types"] = [ct]

    if zone:
        if zone not in ("cn", "intl"):
            return "zone 只能是 'cn' 或 'intl'"
        arguments["zone"] = zone

    effective_max = max(1, min(max_results, 100))
    arguments["max_results"] = effective_max

    if freshness:
        if freshness not in ("day", "week", "month", "year"):
            return "freshness 只能是 day/week/month/year"
        arguments["freshness"] = freshness

    api_key_status = "✅ 已配置" if _load_api_key() else "⚠️  未配置（匿名访问，速率受限）"
    prefix = f"=== AnySearch ({api_key_status}) ===\n"
    return prefix + _call_anysearch("search", arguments)


def any_search_domains(
    domain: Optional[str] = None,
    domains: Optional[str] = None,
) -> str:
    """查询 AnySearch 垂直领域目录。

    在执行垂直搜索前必须调用此接口，获取可用 sub_domain、
    query_format（查询格式）、参数约束、区域要求等信息。

    Args:
        domain: 单个领域名称（如 finance, code, security）
        domains: 多个领域，逗号分隔（最多 5 个），优先于 domain

    Returns:
        Markdown 表格形式的领域目录信息。
    """
    arguments: dict = {}

    if domains:
        domain_list = [d.strip() for d in domains.split(",") if d.strip()]
        if len(domain_list) > 5:
            return "最多支持 5 个领域批量查询"
        invalid = [d for d in domain_list if d not in AVAILABLE_DOMAINS]
        if invalid:
            return f"无效 domain：{', '.join(invalid)}。可用：{', '.join(AVAILABLE_DOMAINS)}"
        arguments["domains"] = domain_list
    elif domain:
        if domain not in AVAILABLE_DOMAINS:
            return f"无效 domain：{domain}。可用：{', '.join(AVAILABLE_DOMAINS)}"
        arguments["domain"] = domain
    else:
        return (
            "请指定 domain 或 domains 参数。\n"
            f"可用领域：{', '.join(AVAILABLE_DOMAINS)}"
        )

    result = _call_anysearch("list_domains", arguments)

    # 如果 API 不支持 list_domains，返回内置的领域说明作为 fallback
    if "not found" in result.lower() or "不存在" in result:
        lines = ["# AnySearch 可用垂直领域", ""]
        lines.append(f"共 {len(AVAILABLE_DOMAINS)} 个领域：{', '.join(AVAILABLE_DOMAINS)}")
        lines.append("")
        lines.append("| 领域 | 说明 |")
        lines.append("|------|------|")
        descriptions = {
            "code": "代码搜索（GitHub、Stack Overflow、技术文档）",
            "tech": "通用技术资讯搜索",
            "finance": "金融数据（股票、基金、外汇、商品、财报）",
            "academic": "学术论文搜索（DOI、期刊、arXiv）",
            "legal": "法律文书、判例、法规检索",
            "security": "安全漏洞（CVE）、安全公告、技术分析",
            "health": "医疗健康信息、药品、疾病资料",
            "education": "教育资源、课程、学校信息",
            "travel": "旅游、酒店、航班、景点信息",
            "ecommerce": "电商商品、价格对比、购物信息",
            "gaming": "游戏资讯、攻略、赛事",
            "film": "电影、剧集、影评",
            "music": "音乐、专辑、歌词、艺人",
            "fashion": "时尚、穿搭、品牌",
            "home": "家居、装修、生活方式",
            "business": "商业新闻、公司、行业",
            "ip": "知识产权、专利、商标",
            "religion": "宗教、文化",
            "geo": "地理、地图、地点",
            "environment": "环境、气候、生态",
            "energy": "能源、电力、新能源",
            "ugc": "用户生成内容（社交平台、论坛）",
        }
        for d in AVAILABLE_DOMAINS:
            lines.append(f"| {d} | {descriptions.get(d, '-')} |")
        lines.append("")
        lines.append("> 提示：使用 `any_search(query=..., domain=..., sub_domain=...)` 进行垂直搜索。")
        lines.append("> 如需精确的 sub_domain 列表和 query_format，请通过 CLI 运行：anysearch list_domains --domain <name>")
        return "\n".join(lines)

    return result


def any_search_batch(
    queries: str,
) -> str:
    """AnySearch 批量并行搜索（2-5 个查询）。

    Args:
        queries: JSON 数组，每个元素是查询对象。
            每个对象支持字段：query(必填), domain, sub_domain,
            content_types, zone, max_results, freshness。
            示例：'[{"query":"AAPL","domain":"finance","sub_domain":"finance.us_stock"},{"query":"python教程"}]'

    Returns:
        所有查询的合并结果。
    """
    try:
        parsed = json.loads(queries)
    except json.JSONDecodeError as e:
        return f"queries 必须是合法 JSON 数组：{e}"

    if not isinstance(parsed, list):
        return "queries 必须是数组格式"
    if len(parsed) < 1 or len(parsed) > 5:
        return "批量搜索支持 1-5 个查询"

    return _call_anysearch("batch_search", {"queries": parsed})


def any_search_extract(
    url: str,
) -> str:
    """AnySearch 网页全文提取（Markdown 格式）。

    比 web_extract 更强：基于搜索引擎缓存 + 智能解析，
    对反爬页面兼容性更好。输出截断在 50,000 字符。

    Args:
        url: 要提取的网页 URL

    Returns:
        Markdown 格式的网页全文。
    """
    if not url.startswith(("http://", "https://")):
        return f"无效 URL：{url}（必须以 http:// 或 https:// 开头）"
    return _call_anysearch("extract", {"url": url})


# ── 内部辅助（供 web_search fallback 调用） ──────────────────────────


def _anysearch_fallback(query: str, limit: int) -> Optional[str]:
    """供 web_search 调用的 fallback 接口，失败返回 None。"""
    try:
        arguments = {"query": query, "max_results": min(limit, 20)}
        result = _call_anysearch("search", arguments)
        if result and not result.startswith(("AnySearch ", "无效", "垂直搜索需要")):
            return f"=== Web Search (AnySearch): {query} ===\n{result}"
    except Exception:
        pass
    return None


# ── 注册为 Agent 工具 ────────────────────────────────────────────────

tool_any_search = function_tool(any_search)
tool_any_search_domains = function_tool(any_search_domains)
tool_any_search_batch = function_tool(any_search_batch)
tool_any_search_extract = function_tool(any_search_extract)

ALL_ANYSEARCH_TOOLS = [
    tool_any_search,
    tool_any_search_domains,
    tool_any_search_batch,
    tool_any_search_extract,
]
