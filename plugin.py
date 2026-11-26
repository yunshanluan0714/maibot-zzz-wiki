"""zzz-wiki 插件主入口 — 适配 maibot_sdk 2.x

提供 ``zzz_wiki_lookup`` Tool：默认实时查询绝区零中文维基（wiki.biligame.com/zzz），
若插件数据目录中存在可选的本地离线语料（aka/、aka/lore/），则优先本地命中。
"""

from __future__ import annotations

from pathlib import Path
import re
import time
from typing import Any
import urllib.parse

from maibot_sdk import MaiBotPlugin, Tool
from maibot_sdk.types import ToolParameterInfo, ToolParamType

from .akads import AkaDataset
from .client import ZzzWikiClient
from .config import ZzzWikiPluginConfig
from .lore import LoreStore
from .wikitext import (
    INFO_KEYS,
    SECTION_PRIORITY,
    STAT_KEYS,
    build_summary,
    clean_wikitext,
    generic_entries,
    parse_template_params,
    split_sections,
    strip_query_suffix,
)

PLUGIN_VERSION = "0.1.0"
MAX_CHARS_CEILING = 4000
MAX_CACHE_ENTRIES = 128

__all__ = [
    "INFO_KEYS",
    "MAX_CHARS_CEILING",
    "PLUGIN_VERSION",
    "SECTION_PRIORITY",
    "STAT_KEYS",
    "ZzzWikiPlugin",
    "build_summary",
    "clean_wikitext",
    "create_plugin",
    "generic_entries",
    "parse_template_params",
    "split_sections",
    "strip_query_suffix",
]


class ZzzWikiPlugin(MaiBotPlugin):
    """绝区零中文维基查询插件。"""

    config_model = ZzzWikiPluginConfig

    def __init__(self) -> None:
        super().__init__()
        self._client: ZzzWikiClient | None = None
        self._aka: AkaDataset | None = None
        self._lore: LoreStore | None = None
        self._cache: dict[str, tuple[float, str]] = {}

    async def on_load(self) -> None:
        cfg = self.config
        self.ctx.logger.info(
            "zzz-wiki %s 已加载 (base=%s, timeout=%ds, max_chars=%d)",
            PLUGIN_VERSION,
            cfg.network.base_url,
            cfg.network.timeout_seconds,
            cfg.result.max_chars,
        )

    async def on_unload(self) -> None:
        await self._close_client()
        self._cache.clear()
        self.ctx.logger.info("zzz-wiki 已卸载")

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        del config_data
        await self._close_client()
        self._cache.clear()
        self.ctx.logger.info("zzz-wiki 配置更新: scope=%s version=%s", scope, version)

    async def _close_client(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            try:
                await client.close()
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.warning("关闭 zzz-wiki 客户端失败: %s", exc)

    def _get_client(self) -> ZzzWikiClient:
        if self._client is None:
            cfg = self.config
            self._client = ZzzWikiClient(
                base_url=cfg.network.base_url,
                timeout_seconds=cfg.network.timeout_seconds,
            )
        return self._client

    def _get_data_dir(self) -> Path | None:
        raw = getattr(getattr(self.ctx, "paths", None), "data_dir", None)
        return Path(raw) if raw else None

    def _get_aka(self) -> AkaDataset | None:
        """akademiya 本地数据集（数据目录 aka/ 缺失时返回 None，走 biligame）。"""
        if self._aka is None:
            data_dir = self._get_data_dir()
            if data_dir is None:
                return None
            self._aka = AkaDataset(data_dir)
        return self._aka

    def _get_lore(self) -> LoreStore | None:
        """biligame 全站词条本地语料库（lore/ 缺失时返回 None）。"""
        if self._lore is None:
            data_dir = self._get_data_dir()
            if data_dir is None:
                return None
            self._lore = LoreStore(data_dir)
        return self._lore

    def _cached(self, key: str) -> str | None:
        ttl = self.config.result.cache_ttl_seconds
        if ttl <= 0:
            return None
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        return None

    def _store(self, key: str, value: str) -> None:
        self._cache[key] = (time.time(), value)
        if len(self._cache) > MAX_CACHE_ENTRIES:
            oldest = min(self._cache, key=lambda k: self._cache[k][0])
            self._cache.pop(oldest, None)

    def _lookup_local(self, query: str, base_url: str, limit: int) -> str | None:
        exact = self._lookup_lore(query, base_url, limit, exact_only=True)
        if exact:
            return exact
        aka = self._get_aka()
        if aka is not None:
            try:
                local_text = aka.lookup(query, limit)
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.warning("akademiya 本地数据集查询失败: %s", exc)
                local_text = None
            if local_text:
                self.ctx.logger.info("zzz_wiki_lookup 本地命中: %s (dataset %s)", query, aka.built_at())
                return local_text

        return self._lookup_lore(query, base_url, limit, exact_only=False)

    def _lookup_lore(self, query: str, base_url: str, limit: int, *, exact_only: bool) -> str | None:
        lore = self._get_lore()
        if lore is not None:
            try:
                lore_title = lore.resolve(query, exact_only=exact_only)
                if lore_title:
                    lore_wikitext = lore.fetch(lore_title)
                    if lore_wikitext:
                        url = f"{base_url}/{urllib.parse.quote(lore_title)}"
                        text = build_summary(lore_title, lore_wikitext, url, limit)
                        self.ctx.logger.info("zzz_wiki_lookup 剧情库命中: %s -> %s", query, lore_title)
                        return text
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.warning("lore 库查询失败: %s", exc)
        return None

    async def _resolve_remote_title(self, client: ZzzWikiClient, query: str) -> tuple[str | None, str]:
        title = await client.resolve_title(query)
        core = strip_query_suffix(query)
        if not title and core and core != query:
            title = await client.resolve_title(core)
        if title:
            return title, ""

        max_candidates = self.config.result.max_candidates
        hits = await client.search(query, max_candidates)
        if not hits and core and core != query:
            hits = await client.search(core, max_candidates)
        if not hits:
            return None, ""
        resolved = hits[0]["title"]
        return resolved, f"（没找到同名词条，按搜索结果「{resolved}」回答）\n"

    async def _lookup_remote(self, query: str, mode: str, base_url: str, limit: int) -> dict[str, Any]:
        client = self._get_client()
        if mode == "search":
            hits = await client.search(query, self.config.result.max_candidates)
            return {"success": True, "content": self._format_hits(hits)}

        title = query
        note = ""
        if mode == "auto":
            resolved, note = await self._resolve_remote_title(client, query)
            if not resolved:
                return {
                    "success": True,
                    "content": f"维基里没查到「{query}」，换个角色名/装备名试试。",
                }
            title = resolved

        cache_key = f"{title}|{limit}"
        cached = self._cached(cache_key)
        if cached:
            return {"success": True, "content": note + cached}

        wikitext = await client.fetch_wikitext(title)
        if not wikitext:
            hits = await client.search(query, self.config.result.max_candidates)
            return {
                "success": True,
                "content": f"「{title}」没有正文，可能相关的是：\n" + self._format_hits(hits),
            }

        url = f"{base_url}/{urllib.parse.quote(title)}"
        text = build_summary(title, wikitext, url, limit)
        self._store(cache_key, text)
        return {"success": True, "content": note + text}

    @Tool(
        "zzz_wiki_lookup",
        description=(
            "查询绝区零（Zenless Zone Zero）中文维基：角色属性/天赋/配装推荐/阵容搭配，"
            "以及驱动盘、音擎、怪物、材料等词条。聊到绝区零的角色怎么配、某个BOSS怎么打、"
            "某个装备什么效果时调用它，比凭印象编更可靠。"
        ),
        parameters=[
            ToolParameterInfo(
                name="query",
                param_type=ToolParamType.STRING,
                description="要查的词条名或关键词，如「安比」「妮可配装」「山大王驱动盘」",
                required=True,
            ),
            ToolParameterInfo(
                name="mode",
                param_type=ToolParamType.STRING,
                description="auto=先按标题精确查，查不到再搜索（默认）；page=只查词条正文；search=只列候选词条",
                required=False,
                enum_values=["auto", "page", "search"],
                default="auto",
            ),
            ToolParameterInfo(
                name="max_chars",
                param_type=ToolParamType.INTEGER,
                description="返回正文最大字符数，留空用配置默认值",
                required=False,
            ),
        ],
    )
    async def handle_zzz_wiki_lookup(
        self,
        query: str = "",
        mode: str = "auto",
        max_chars: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        normalized = str(query or "").strip()
        if not normalized:
            return {"success": False, "content": "查询词不能为空。", "error": "empty_query"}

        default_chars = self.config.result.max_chars
        try:
            limit = int(max_chars) if max_chars is not None else default_chars
        except (TypeError, ValueError):
            limit = default_chars
        limit = max(200, min(limit, MAX_CHARS_CEILING))

        norm_mode = str(mode or "auto").strip() or "auto"
        base_url = self.config.network.base_url.rstrip("/")

        if norm_mode != "search":
            local_text = self._lookup_local(normalized, base_url, limit)
            if local_text:
                return {"success": True, "content": local_text}

        try:
            return await self._lookup_remote(normalized, norm_mode, base_url, limit)
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.error("zzz_wiki_lookup 执行异常: %s", exc, exc_info=True)
            return {"success": False, "content": f"绝区零维基查询失败: {exc}", "error": str(exc)}

    @staticmethod
    def _format_hits(hits: list[dict[str, str]]) -> str:
        if not hits:
            return "维基里没有搜到相关词条。"
        lines = ["候选词条："]
        for index, hit in enumerate(hits, start=1):
            snippet = re.sub(r"<[^>]+>", "", hit.get("snippet", ""))
            snippet = re.sub(r"\s+", " ", snippet).strip()
            lines.append(f"{index}. {hit.get('title', '')}" + (f" — {snippet[:80]}" if snippet else ""))
        return "\n".join(lines)


def create_plugin() -> ZzzWikiPlugin:
    """Runner 通过此工厂函数实例化插件。"""
    return ZzzWikiPlugin()
