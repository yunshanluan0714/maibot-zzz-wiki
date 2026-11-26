"""可选全站词条本地语料库（剧情/背景/世界观查询加速用）。

若用户在插件数据目录下自备以下文件，将按需懒读并交由 build_summary 清洗摘要：
    <ctx.paths.data_dir>/aka/lore/
        index.json              {"built_at","titles":[...]}
        <urlencoded 标题>.json  {"title","wikitext"}

目录不存在时返回 None，由调用方直接查询在线维基接口。
"""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Optional
import urllib.parse

_SUFFIX = (
    "的剧情",
    "剧情",
    "的故事",
    "故事",
    "的背景",
    "背景",
    "的身世",
    "身世",
    "的设定",
    "设定",
    "的介绍",
    "介绍",
    "是谁",
    "资料",
    "档案",
)


class LoreStore:
    """全站词条标题索引 + 懒加载 wikitext。"""

    def __init__(self, data_dir: Optional[Path]) -> None:
        self.data_dir = Path(data_dir) if data_dir else None
        self._stamp: float | None = None
        self._titles: list[str] = []
        self._title_set: set[str] = set()
        self._built_at = ""
        self._loaded_at = 0.0

    def _dir(self) -> Optional[Path]:
        if not self.data_dir:
            return None
        lore_dir = self.data_dir / "aka" / "lore"
        return lore_dir if lore_dir.is_dir() else None

    def _ensure_loaded(self) -> bool:
        lore_dir = self._dir()
        if not lore_dir:
            return False
        try:
            stamp = (lore_dir / "index.json").stat().st_mtime
        except OSError:
            return False
        if self._loaded_at and stamp == self._stamp:
            return True
        try:
            idx = json.loads((lore_dir / "index.json").read_text(encoding="utf-8"))
            self._titles = [str(t) for t in idx.get("titles", [])] if isinstance(idx, dict) else []
            self._built_at = str(idx.get("built_at", "")) if isinstance(idx, dict) else ""
        except (OSError, ValueError):
            self._titles = []
            self._built_at = ""
        self._title_set = set(self._titles)
        self._stamp = stamp
        self._loaded_at = time.time()
        return True

    @property
    def ready(self) -> bool:
        return self._ensure_loaded()

    def built_at(self) -> str:
        return self._built_at if self._ensure_loaded() else ""

    # -------------------------------------------------- 检索

    @staticmethod
    def _candidates(query: str) -> list[str]:
        """查询词 → 依次尝试的标题候选。"""
        q = " ".join((query or "").split())
        if not q:
            return []
        out = [q]
        stripped = q
        for _ in range(3):
            for suf in _SUFFIX:
                if stripped.endswith(suf) and len(stripped) > len(suf):
                    stripped = stripped[: -len(suf)].rstrip(" 的")
                    out.append(stripped)
                    break
            else:
                break
        return out

    def resolve(self, query: str, *, exact_only: bool = False) -> Optional[str]:
        """查询词 → 词条标题（精确 → 剥后缀精确 → 子串包含选最短主词条）。"""
        if not self._ensure_loaded():
            return None
        cands = self._candidates(query)
        for cand in cands:
            if cand in self._title_set:
                return cand
        if exact_only:
            return None
        for cand in cands:
            if len(cand) < 2:
                continue
            hits = [title for title in self._titles if cand in title and len(title) <= len(cand) + 12]
            if hits:
                hits.sort(key=len)
                return hits[0]
        return None

    def fetch(self, title: str) -> str:
        lore_dir = self._dir()
        if not lore_dir or not title:
            return ""
        fname = urllib.parse.quote(title, safe="") + ".json"
        try:
            obj = json.loads((lore_dir / fname).read_text(encoding="utf-8"))
            return str(obj.get("wikitext", "")) if isinstance(obj, dict) else ""
        except (OSError, ValueError):
            return ""
