"""MediaWiki API 客户端（绝区零 B 站维基）。"""

from __future__ import annotations

import asyncio
import urllib.parse
from typing import Any

import aiohttp

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class ZzzWikiClient:
    """最小的 MediaWiki 客户端，只做搜索 / 标题解析 / 正文读取。"""

    def __init__(self, base_url: str, timeout_seconds: int = 15) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_url = f"{self.base_url}/api.php"
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # 站点 WAF 会拦非浏览器 UA（返回 567），必须带 UA + Referer
            self._session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers={
                    "User-Agent": DEFAULT_UA,
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    "Referer": f"{self.base_url}/",
                },
            )
        return self._session

    async def _api(self, params: dict[str, Any], referer: str | None = None) -> dict[str, Any]:
        session = await self._get_session()
        payload = {"format": "json", "formatversion": "2", **params}
        headers = {"Referer": referer} if referer else None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with session.get(self.api_url, params=payload, headers=headers) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"HTTP {resp.status}")
                    return await resp.json(content_type=None)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"维基接口请求失败: {last_error}")

    async def search(self, query: str, limit: int = 5) -> list[dict[str, str]]:
        data = await self._api(
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": str(max(1, min(limit, 10))),
                "srprop": "snippet",
            }
        )
        hits = (data.get("query") or {}).get("search") or []
        return [{"title": str(hit.get("title", "")), "snippet": str(hit.get("snippet", ""))} for hit in hits]

    async def resolve_title(self, query: str) -> str | None:
        """精确/重定向解析标题；不存在返回 None。"""
        data = await self._api(
            {
                "action": "query",
                "titles": query,
                "redirects": "1",
                "prop": "info",
            }
        )
        pages = (data.get("query") or {}).get("pages") or []
        if not pages:
            return None
        page = pages[0]
        if page.get("missing") or page.get("invalid"):
            return None
        return str(page.get("title") or "") or None

    async def fetch_wikitext(self, title: str) -> str:
        data = await self._api(
            {
                "action": "query",
                "prop": "revisions",
                "rvslots": "main",
                "rvprop": "content",
                "titles": title,
                "redirects": "1",
            },
            referer=f"{self.base_url}/{urllib.parse.quote(title)}",
        )
        pages = (data.get("query") or {}).get("pages") or []
        if not pages or pages[0].get("missing"):
            return ""
        revisions = pages[0].get("revisions") or []
        if not revisions:
            return ""
        slots = revisions[0].get("slots") or {}
        main = slots.get("main") or {}
        return str(main.get("content") or "")

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
