import asyncio
import httpx
import re
import urllib.parse
from bs4 import BeautifulSoup
from datetime import datetime
from config.logger import setup_logging
from plugins_func.register import (
    register_function,
    ToolType,
    ActionResponse,
    Action,
)
from typing import TYPE_CHECKING, List, Dict, Any

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

WEB_SEARCH_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "【全网深度多源联网搜索与事实对比推理引擎】\n"
            "当遇到以下任意情况时，小智必须主动调用此工具联网检索最新互联网事实，严禁盲目凭空猜测或产生幻觉：\n"
            "1. 实时时事与最新新闻（如今天/近期国内国际大事件、突发要闻、热点资讯）；\n"
            "2. 实时行情与动态数据（如今日汇率、金价、股价走势、赛事比分、新规政策）；\n"
            "3. 科技与产品发布进展（如最新大模型进展、芯片突破、手机汽车新品发布会）；\n"
            "4. 未知实体、概念、公司、人物现状或特定知识事实查询；\n"
            "5. 任何你记忆库中不确定或需要核实确认的提问。\n"
            "本工具会自动从 Bing 新闻、全网权威网页及全球多源搜索引擎进行并行检索，并进行多方事实比对与交叉推理。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "高度概括、精准的搜索关键词或核心检索短语（去除无意义语气词，提取核心实体与意图，例如'2026年最新科技重大突破'、'中国航天发射最新进展'、'今天国际金价'）。",
                }
            },
            "required": ["query"],
        },
    },
}

class SmartMultiWebSearcher:
    """多引擎全网实时搜索与多源事实比对引擎"""

    def __init__(self, tavily_key: str = None, metaso_key: str = None):
        self.tavily_key = tavily_key
        self.metaso_key = metaso_key

    async def search_bing_news(self, query: str, max_results: int = 4) -> List[Dict[str, str]]:
        """检索 Bing 实时新闻频道，获取第一手权威媒体报道"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        url = f"https://www.bing.com/news/search?q={urllib.parse.quote(query)}&setlang=zh-Hans"
        results = []
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(4.5, connect=2.0), headers=headers) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for card in soup.find_all("div", class_="news-card"):
                        title_elem = card.find("a", class_="title")
                        snippet_elem = card.find("div", class_="snippet")
                        source_elem = card.find("div", class_="source")
                        
                        title = title_elem.get_text(strip=True) if title_elem else ""
                        snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""
                        source = source_elem.get_text(strip=True) if source_elem else "权威新闻媒体"
                        
                        if title and snippet:
                            results.append({
                                "source": f"最新新闻 ({source})",
                                "title": title,
                                "snippet": snippet
                            })
                        if len(results) >= max_results:
                            break
        except Exception as e:
            logger.bind(tag=TAG).debug(f"Bing News 检索失败: {e}")
        return results

    async def search_ddg_cn(self, query: str, max_results: int = 4) -> List[Dict[str, str]]:
        """检索 DuckDuckGo 中文互联网深度内容"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        url = "https://html.duckduckgo.com/html/"
        results = []
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(4.5, connect=2.0), headers=headers) as client:
                resp = await client.post(url, data={"q": query, "kl": "cn-zh"})
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for res in soup.find_all("div", class_="result"):
                        a = res.find("a", class_="result__snippet")
                        title_elem = res.find("h2", class_="result__title")
                        title = title_elem.get_text(strip=True) if title_elem else ""
                        snippet = a.get_text(strip=True) if a else ""
                        if title and snippet and len(snippet) > 15:
                            results.append({
                                "source": "全网精选",
                                "title": title,
                                "snippet": snippet
                            })
                        if len(results) >= max_results:
                            break
        except Exception as e:
            logger.bind(tag=TAG).debug(f"DDG-CN 检索失败: {e}")
        return results

    async def search_ddg_global(self, query: str, max_results: int = 3) -> List[Dict[str, str]]:
        """检索 DuckDuckGo 全球综合网络"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        url = "https://html.duckduckgo.com/html/"
        results = []
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(4.5, connect=2.0), headers=headers) as client:
                resp = await client.post(url, data={"q": query, "kl": "wt-wt"})
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for res in soup.find_all("div", class_="result"):
                        a = res.find("a", class_="result__snippet")
                        title_elem = res.find("h2", class_="result__title")
                        title = title_elem.get_text(strip=True) if title_elem else ""
                        snippet = a.get_text(strip=True) if a else ""
                        if title and snippet and len(snippet) > 15:
                            results.append({
                                "source": "全球网络",
                                "title": title,
                                "snippet": snippet
                            })
                        if len(results) >= max_results:
                            break
        except Exception as e:
            logger.bind(tag=TAG).debug(f"DDG-Global 检索失败: {e}")
        return results

    async def search_tavily(self, query: str, max_results: int = 3) -> List[Dict[str, str]]:
        """如果配置了 Tavily 密钥，调用高级 AI 搜索"""
        if not self.tavily_key:
            return []
        url = "https://api.tavily.com/search"
        headers = {"Authorization": f"Bearer {self.tavily_key}", "Content-Type": "application/json"}
        payload = {"query": query, "max_results": max_results, "search_depth": "advanced"}
        results = []
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data.get("results", []):
                        results.append({
                            "source": "Tavily AI 知识库",
                            "title": item.get("title", ""),
                            "snippet": item.get("content", "")
                        })
        except Exception as e:
            logger.bind(tag=TAG).debug(f"Tavily 检索失败: {e}")
        return results

    async def search_and_synthesize(self, query: str) -> str:
        """并行多引擎全网检索 + 自动去重 + 多源事实对比推理组装"""
        tasks = [
            self.search_bing_news(query, 3),
            self.search_ddg_cn(query, 4),
            self.search_ddg_global(query, 3),
        ]
        if self.tavily_key:
            tasks.append(self.search_tavily(query, 3))

        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        merged_items = []
        seen_titles = set()
        for res_list in raw_results:
            if isinstance(res_list, list):
                for item in res_list:
                    if not item.get("title") or not item.get("snippet"):
                        continue
                    # 简单归一化标题去重
                    title_norm = re.sub(r'[\s\-_|:：·]+', '', item["title"][:18].lower())
                    if title_norm not in seen_titles:
                        seen_titles.add(title_norm)
                        merged_items.append(item)

        if not merged_items:
            logger.bind(tag=TAG).warning(f"未能获取到《{query}》的联网搜索结果")
            return f"未能从互联网检索到关于《{query}》的明确最新记录。请向用户友好说明，并建议换个关键词再次查询。"

        now_str = datetime.now().strftime("%Y年%m月%d日 %H:%M")
        
        output_parts = [
            f"【🌐 互联网全网多源实时检索与对比分析结果】",
            f"检索主题：《{query}》 | 检索基准时间：{now_str}",
            f"已聚合来自权威新闻媒体、百科与全网共 {len(merged_items)} 个独立信源：\n"
        ]

        for i, item in enumerate(merged_items[:7], 1):
            output_parts.append(f"📌 信源 {i} [{item['source']}]：{item['title']}")
            output_parts.append(f"   核心事实：{item['snippet']}\n")

        output_parts.append(
            "【🧠 智能多源对比与逻辑推理分析指令】\n"
            "1. 交叉比对共识：比对上述各独立信源的核心信息与时间线，提炼共同确凿的事实要点，自动排除陈旧、孤证或矛盾信息。\n"
            "2. 深度推理确认：根据多源信息综合分析，对用户关心的核心问题进行严密推导，得出客观、准确、有说服力的最终答案。\n"
            "3. 语音播报规范：小智作为智能语音助理，请用亲切、流畅、自然的口语直接为用户播报结论与关键要点（不要朗读网址、代码或 Markdown 格式符号）。"
        )

        return "\n".join(output_parts)


@register_function("web_search", WEB_SEARCH_FUNCTION_DESC, ToolType.SYSTEM_CTL)
async def web_search(conn: "ConnectionHandler", query: str = None):
    logger.bind(tag=TAG).info(f"🌐 触发多引擎智能联网搜索 | 关键词: '{query}'")
    if not query or not query.strip():
        return ActionResponse(Action.REQLLM, "请提供要搜索的具体关键词或问题。", None)

    web_search_config = {}
    if conn and hasattr(conn, "config") and conn.config:
        web_search_config = conn.config.get("plugins", {}).get("web_search", {})

    tavily_key = web_search_config.get("api_key") if web_search_config.get("provider") == "tavily" else None
    metaso_key = web_search_config.get("api_key") if web_search_config.get("provider") == "metaso" else None

    searcher = SmartMultiWebSearcher(tavily_key=tavily_key, metaso_key=metaso_key)
    result_text = await searcher.search_and_synthesize(query.strip())
    
    logger.bind(tag=TAG).info(f"✅ 联网搜索与多源推理准备就绪，长度: {len(result_text)} 字符")
    return ActionResponse(Action.REQLLM, result_text, None)
