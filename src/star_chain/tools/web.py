"""工具层 — Web Search 与页面提取。

提供 web_search, web_extract 两个工具供 Agent 使用。

实现方式：
  - web_search: 多搜索引擎 fallback 策略
    * 优先 Bing (https://www.bing.com/search) - 全球可用，结果质量高
    * 备选 Sogou (https://www.sogou.com/web) - 中文环境友好
    * DuckDuckGo 作为末级 fallback（某些环境下被拦截）
  - web_extract: 通过 httpx 抓取页面内容，做基础 HTML→Markdown 转换
"""

import html
import re
import time
from typing import Optional, Tuple, List
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


# ── 搜索引擎解析器 ────────────────────────────────────────────────────


def _parse_bing_results(html_text: str, limit: int) -> List[Tuple[str, str, str]]:
    """解析 Bing 搜索结果页 HTML。

    Bing 结果结构：
      <li class="b_algo">
        <h2><a href="URL">TITLE</a></h2>
        <p class="b_caption / b_snippet">SNIPPET</p>
      </li>
    """
    results: List[Tuple[str, str, str]] = []
    for m in re.finditer(r'<li class="b_algo"[^>]*>(.*?)</li>', html_text, re.DOTALL):
        block = m.group(1)
        link_m = re.search(r'<h2[^>]*>\s*<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>\s*</h2>', block, re.DOTALL)
        if not link_m:
            link_m = re.search(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not link_m:
            continue
        url = link_m.group(1).strip()
        title = html.unescape(re.sub(r"<[^>]+>", "", link_m.group(2))).strip()
        snippet = ""
        snip_m = re.search(
            r'<(?:p|div)[^>]*class="[^"]*(?:b_caption|b_snippet|b_richcard)[^"]*"[^>]*>(.*?)</(?:p|div)>',
            block,
            re.DOTALL,
        )
        if snip_m:
            snippet = html.unescape(re.sub(r"<[^>]+>", "", snip_m.group(1))).strip()
        results.append((title, url, snippet))
        if len(results) >= limit:
            break
    return results


def _parse_sogou_results(html_text: str, limit: int) -> List[Tuple[str, str, str]]:
    """解析 Sogou 搜索结果页 HTML。

    Sogou 结果结构：
      <div class="vrwrap">
        <h3><a href="/link?url=...">TITLE</a></h3>
        <p class="str-text-info">SNIPPET</p>
      </div>
    """
    results: List[Tuple[str, str, str]] = []
    for m in re.finditer(r'<div class="vrwrap"[^>]*>(.*?)</div>\s*</div>', html_text, re.DOTALL):
        block = m.group(1)
        link_m = re.search(r'<h3[^>]*>(.*?)</h3>', block, re.DOTALL)
        if not link_m:
            continue
        h3_content = link_m.group(1)
        url_m = re.search(r'href="([^"]+)"', h3_content)
        title = html.unescape(re.sub(r"<[^>]+>", "", h3_content)).strip()
        raw_url = url_m.group(1) if url_m else ""
        if raw_url.startswith("/link?"):
            url = f"https://www.sogou.com{raw_url}"
        else:
            url = raw_url
        snippet = ""
        snip_m = re.search(
            r'<(?:p|div)[^>]*class="[^"]*(?:str-text-info|str_info|str-text)[^"]*"[^>]*>(.*?)</(?:p|div)>',
            block,
            re.DOTALL,
        )
        if snip_m:
            snippet = html.unescape(re.sub(r"<[^>]+>", "", snip_m.group(1))).strip()
        results.append((title, url, snippet))
        if len(results) >= limit:
            break
    return results


def _parse_duckduckgo_results(html_text: str, limit: int) -> List[Tuple[str, str, str]]:
    """解析 DuckDuckGo Lite 结果页 HTML（作为 fallback）。"""
    results: List[Tuple[str, str, str]] = []
    pattern = re.compile(
        r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
        r".*?"
        r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    for m in pattern.finditer(html_text):
        url = m.group(1).strip()
        title = html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
        snippet = html.unescape(re.sub(r"<[^>]+>", "", m.group(3))).strip()
        results.append((title, url, snippet))
        if len(results) >= limit:
            break

    if not results:
        alt_pattern = re.compile(
            r'<a[^>]*rel="nofollow"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            re.DOTALL,
        )
        for m in alt_pattern.finditer(html_text):
            url = m.group(1).strip()
            title = html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
            results.append((title, url, ""))
            if len(results) >= limit:
                break
    return results


def _search_bing(query: str, limit: int) -> Optional[List[Tuple[str, str, str]]]:
    """通过 Bing 搜索，返回结果列表或 None（失败时）。"""
    try:
        with httpx.Client(timeout=SEARCH_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(
                "https://www.bing.com/search",
                params={"q": query},
                headers={"User-Agent": USER_AGENT},
            )
            resp.raise_for_status()
            results = _parse_bing_results(resp.text, limit)
            return results if results else None
    except Exception:
        return None


def _search_sogou(query: str, limit: int) -> Optional[List[Tuple[str, str, str]]]:
    """通过 Sogou 搜索（中文环境友好），返回结果列表或 None（失败时）。"""
    try:
        with httpx.Client(timeout=SEARCH_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(
                "https://www.sogou.com/web",
                params={"query": query},
                headers={"User-Agent": USER_AGENT},
            )
            resp.raise_for_status()
            results = _parse_sogou_results(resp.text, limit)
            return results if results else None
    except Exception:
        return None


def _search_duckduckgo(query: str, limit: int) -> Optional[List[Tuple[str, str, str]]]:
    """通过 DuckDuckGo Lite 搜索（末级 fallback）。"""
    try:
        with httpx.Client(timeout=SEARCH_TIMEOUT, follow_redirects=True) as client:
            resp = client.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
                headers={"User-Agent": USER_AGENT},
            )
            resp.raise_for_status()
            results = _parse_duckduckgo_results(resp.text, limit)
            return results if results else None
    except Exception:
        return None


def web_search(
    query: str,
    limit: int = 5,
) -> str:
    """执行 Web 搜索（多引擎 fallback 策略）。

    按优先级依次尝试：Bing → Sogou → DuckDuckGo。
    任一引擎返回有效结果即停止，避免无谓等待。

    Args:
        query: 搜索关键词
        limit: 返回结果数量（默认 5，最大 20）

    Returns:
        格式化搜索结果列表（标题 + URL + 摘要）。
    """
    effective_limit = min(limit, 20)
    errors: List[str] = []

    # 1. Bing（首选）
    results = _search_bing(query, effective_limit)
    if results:
        engine = "Bing"
    else:
        errors.append("Bing: 无结果或不可达")
        # 2. Sogou（备选，中文友好）
        results = _search_sogou(query, effective_limit)
        if results:
            engine = "Sogou"
        else:
            errors.append("Sogou: 无结果或不可达")
            # 3. DuckDuckGo（末级 fallback）
            results = _search_duckduckgo(query, effective_limit)
            if results:
                engine = "DuckDuckGo"
            else:
                errors.append("DuckDuckGo: 无结果或不可达")
                return (
                    f"所有搜索引擎均不可用或无结果。\n"
                    f"查询：{query}\n"
                    f"详情：{'; '.join(errors)}"
                )

    lines = [f"=== Web Search ({engine}): {query} ==="]
    for i, (title, url, snippet) in enumerate(results, 1):
        lines.append(f"\n{i}. {title}")
        lines.append(f"   URL: {url}")
        if snippet:
            snippet_clean = snippet[:200] + ("..." if len(snippet) > 200 else "")
            lines.append(f"   {snippet_clean}")

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
