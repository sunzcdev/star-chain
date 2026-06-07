"""工具层 — Web Search 与页面提取。

提供 web_search, web_extract 两个工具供 Agent 使用。

实现方式：
  - web_search: 通过 DuckDuckGo Lite 接口做关键词搜索（无需 API Key）
  - web_extract: 通过 httpx 抓取页面内容，做基础 HTML→Markdown 转换
"""

import re
import time
from typing import Optional
from urllib.parse import quote_plus, urlparse

import httpx
from agents import function_tool

# ── 常量 ──────────────────────────────────────────────────────────────

SEARCH_TIMEOUT = 15      # 搜索超时（秒）
FETCH_TIMEOUT = 30       # 页面抓取超时（秒）
MAX_PAGE_CHARS = 50_000  # 单页最大字符数
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# ── HTML → 纯文本 简易转换 ─────────────────────────────────────────


def _html_to_text(html: str, url: str = "") -> str:
    """极简 HTML 转文本，无需外部依赖。"""
    # 移出 <script> 和 <style> 块
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<nav[^>]*>.*?</nav>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<footer[^>]*>.*?</footer>", "", html, flags=re.DOTALL | re.IGNORECASE)

    # 替换 block 元素为换行
    html = re.sub(r"</?(?:p|div|h[1-6]|li|tr|blockquote|br|hr|section|article)[^>]*>", "\n", html, flags=re.IGNORECASE)

    # 替换 <a href> 为 [text](url) 形式
    html = re.sub(
        r'<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        r"[\2](\1)",
        html,
        flags=re.IGNORECASE,
    )

    # 移除所有其他 HTML 标签
    html = re.sub(r"<[^>]+>", " ", html)

    # 解码 HTML 实体
    html = html.replace("&nbsp;", " ").replace("&amp;", "&")
    html = html.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    html = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), html)

    # 压缩空白
    html = re.sub(r"\n\s*\n", "\n\n", html)
    html = re.sub(r" {2,}", " ", html)
    lines = [l.strip() for l in html.split("\n")]
    lines = [l for l in lines if l]
    text = "\n".join(lines)

    if len(text) > MAX_PAGE_CHARS:
        text = text[:MAX_PAGE_CHARS] + f"\n\n... [截断: 超过 {MAX_PAGE_CHARS} 字符]"

    return text.strip()


# ── 工具函数 ──────────────────────────────────────────────────────────


def web_search(
    query: str,
    limit: int = 5,
) -> str:
    """执行 Web 搜索（通过 DuckDuckGo Lite 接口）。

    Args:
        query: 搜索关键词
        limit: 返回结果数量（默认 5，最大 20）

    Returns:
        格式化搜索结果列表（标题 + URL + 摘要）。
    """
    effective_limit = min(limit, 20)
    try:
        with httpx.Client(timeout=SEARCH_TIMEOUT, follow_redirects=True) as client:
            resp = client.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
                headers={"User-Agent": USER_AGENT},
            )
            resp.raise_for_status()
            html = resp.text
    except httpx.TimeoutException:
        return f"搜索超时（{SEARCH_TIMEOUT}s）：{query}"
    except httpx.HTTPStatusError as e:
        return f"搜索 HTTP 错误：{e.response.status_code}"
    except Exception as e:
        return f"搜索出错：{e}"

    # 解析 DuckDuckGo 结果页
    # 结果在 <div class="result"> 中
    results = []
    # 用正则提取每个结果块
    pattern = re.compile(
        r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
        r".*?"
        r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    for m in pattern.finditer(html):
        url = m.group(1).strip()
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        snippet = re.sub(r"<[^>]+>", "", m.group(3)).strip()
        results.append((title, url, snippet))
        if len(results) >= effective_limit:
            break

    if not results:
        # fallback: 尝试更宽松的匹配
        alt_pattern = re.compile(
            r'<a[^>]*rel="nofollow"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            re.DOTALL,
        )
        for m in alt_pattern.finditer(html):
            url = m.group(1).strip()
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            results.append((title, url, ""))
            if len(results) >= effective_limit:
                break

    if not results:
        return f"未找到「{query}」的搜索结果"

    lines = [f"=== Web Search: {query} ==="]
    for i, (title, url, snippet) in enumerate(results, 1):
        lines.append(f"\n{i}. {title}")
        lines.append(f"   URL: {url}")
        if snippet:
            lines.append(f"   {snippet[:200]}{'...' if len(snippet) > 200 else ''}")

    return "\n".join(lines)


def web_extract(
    url: str,
) -> str:
    """抓取并提取网页文本内容。

    通过 httpx 获取页面 HTML 并转换为纯文本。
    支持普通网页和 Markdown 文件（.md/.txt 结尾的 URL 直接返回原文）。

    Args:
        url: 要提取的网页 URL

    Returns:
        页面文本内容标题 + 正文。

    Raises:
        ValueError: URL 格式无效
    """
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return f"无效 URL：{url}"

    # 纯文本 URL 直接返回
    if any(url.endswith(ext) for ext in (".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".xml")):
        try:
            with httpx.Client(timeout=FETCH_TIMEOUT, follow_redirects=True) as client:
                resp = client.get(url, headers={"User-Agent": USER_AGENT})
                resp.raise_for_status()
                content = resp.text
                if len(content) > MAX_PAGE_CHARS:
                    content = content[:MAX_PAGE_CHARS] + f"\n... [截断]"
                return f"来源: {url}\n{'-' * 60}\n{content.strip()}"
        except Exception as e:
            return f"下载失败：{url}\n错误：{e}"

    try:
        with httpx.Client(
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            html = resp.text
    except httpx.TimeoutException:
        return f"页面加载超时（{FETCH_TIMEOUT}s）：{url}"
    except httpx.HTTPStatusError as e:
        return f"HTTP 错误 {e.response.status_code}：{url}"
    except Exception as e:
        return f"页面加载失败：{url}\n错误：{e}"

    # 提取 title
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else url

    text = _html_to_text(html, url)

    return f"标题: {title}\n来源: {url}\n{'-' * 60}\n{text}"


# ── 注册为 Agent 工具 ────────────────────────────────────────────────

tool_web_search = function_tool(web_search)
tool_web_extract = function_tool(web_extract)

ALL_WEB_TOOLS = [tool_web_search, tool_web_extract]
