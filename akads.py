"""可选本地结构化数据集访问层（zzz-wiki 插件）。

若用户在插件数据目录下自备 aka/ 子目录，可优先从本地结构化数据回答：
    <ctx.paths.data_dir>/aka/
        meta.json        构建信息（built_at / counts / source）
        characters.json  角色索引 [{id,name,rarity,element,camp}]
        weapons.json     音擎索引 [{id,name,rarity}]
        zh2id.json       中文名 -> 角色ID
        char/<id>.json   角色完整结构化对象
        weapon/<id>.json 音擎完整结构化对象
        build/<id>.json  养成配装摘录

目录不存在或 lookup() 未命中时返回 None，由调用方自动回落在线维基接口。
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import time
from typing import Any, Optional

ELEMENT_ZH = {
    "Electric": "电属性",
    "Fire": "火属性",
    "Ice": "冰属性",
    "Physical": "物理属性",
    "Ether": "以太属性",
}
FACTION_ZH = {
    "Cunning Hares": "狡兔屋",
    "Belobog Heavy Industries": "白祇重工",
    "Criminal Investigation Special Response Team": "刑侦特勤组",
    "Hollow Special Operations Section 6": "对空洞六课",
    "Stars of Lyra": "天琴座",
    "Silver Squad": "白银小队",
    "Obol Squad": "奥波勒斯小队",
    "Mockingbird": "反舌鸟",
    "Gentle House": "温柔之家",
    "Section 6": "对空洞六课",
}
STAT_ZH = {
    "HP": "生命值",
    "ATK": "攻击力",
    "DEF": "防御力",
    "Impact": "冲击力",
    "Crit Rate": "暴击率",
    "Crit DMG": "暴击伤害",
    "Anomaly Mastery": "异常掌控",
    "Anomaly Proficiency": "异常精通",
    "PEN Ratio": "穿透率",
    "PEN": "穿透值",
    "Energy Regen": "能量自动回复",
}
_SKILL_GROUPS_ZH = (
    ("Basic", "普通攻击"),
    ("Dodge", "闪避"),
    ("Special", "特殊技"),
    ("Chain", "连携技"),
    ("Assist", "支援突击"),
)

# 本地查询专用的后缀剥离（比 biligame 的更激进，纯本地无歧义代价）
_LOCAL_SUFFIX = re.compile(
    r"(的)?(专属音擎|驱动盘|技能|天赋|影画|面板|配装|攻略|强度|材料|核心技|基础)$"
)
_BUILD_HINTS = (
    "养成",
    "配装",
    "驱动盘",
    "音擎推荐",
    "词条",
    "配队",
    "加点",
    "培养",
    "攻略",
    "毕业",
    "怎么练",
    "练度",
    "套装",
)

_TAG_RE = re.compile(r"</?[a-zA-Z][^<>]{0,60}>")
_ICONMAP_RE = re.compile(r"<IconMap:[^<>]*>")
_BRACE_ICON_RE = re.compile(r"\{[^{}\n]*?(?:icon|Icon)[^{}\n]*\}")
_NAME_JUNK_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _name_key(text: Any) -> str:
    """取名用的归一化键：去掉括号/空格/连字符，只留字母数字和汉字。"""
    return _NAME_JUNK_RE.sub("", str(text or "").lower())


def _safe_id(raw_id: str) -> str:
    text = str(raw_id or "").strip()
    return text if _SAFE_ID_RE.match(text) else ""


def _clean(text: Any) -> str:
    """去掉 RSC 描述里的颜色/图标标记。"""
    if not isinstance(text, str):
        return ""
    text = _TAG_RE.sub("", text)
    text = _ICONMAP_RE.sub("", text)
    text = _BRACE_ICON_RE.sub("", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _trim(text: str, limit: int) -> str:
    cleaned = text.strip()
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def _num(val: Any) -> str:
    """数值渲染：整数去掉多余的 .0，浮点紧凑展示。"""
    if isinstance(val, (int, float)):
        if isinstance(val, float) and val == int(val):
            return str(int(val))
        return f"{val:g}"
    return str(val)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


class AkaDataset:
    """本地数据集读取器；带 mtime 感知的进程内缓存。"""

    def __init__(self, data_dir: Optional[Path]) -> None:
        self.data_dir = Path(data_dir) if data_dir else None
        self._stamp: float | None = None
        self._zh2id: dict[str, str] = {}
        self._char_index: list[dict[str, Any]] = []
        self._weapon_index: list[dict[str, Any]] = []
        self._weapon_names: dict[str, str] = {}
        self._meta: dict[str, Any] = {}
        self._loaded_at = 0.0

    # -------------------------------------------------- 加载

    def _dir(self) -> Optional[Path]:
        if not self.data_dir:
            return None
        aka_dir = self.data_dir / "aka"
        return aka_dir if aka_dir.is_dir() else None

    def _ensure_loaded(self) -> bool:
        aka_dir = self._dir()
        if not aka_dir:
            return False
        try:
            stamp = (aka_dir / "meta.json").stat().st_mtime
        except OSError:
            return False
        if self._loaded_at and stamp == self._stamp:
            return True

        zh2id = _read_json(aka_dir / "zh2id.json", {})
        self._zh2id = zh2id if isinstance(zh2id, dict) else {}
        chars = _read_json(aka_dir / "characters.json", [])
        self._char_index = chars if isinstance(chars, list) else []
        weapons = _read_json(aka_dir / "weapons.json", [])
        self._weapon_index = weapons if isinstance(weapons, list) else []

        self._weapon_names = {}
        for entry in self._weapon_index:
            if not isinstance(entry, dict):
                continue
            key = _name_key(entry.get("name"))
            if key and entry.get("id") is not None:
                self._weapon_names[key] = str(entry["id"])

        gloss = _read_json(aka_dir / "glossary.json", {})
        if isinstance(gloss, dict) and isinstance(gloss.get("weapon"), dict):
            for en_name, zh_name in gloss["weapon"].items():
                wid = self._weapon_names.get(_name_key(en_name))
                zh_key = _name_key(zh_name)
                if wid and zh_key:
                    self._weapon_names[zh_key] = wid

        meta = _read_json(aka_dir / "meta.json", {})
        self._meta = meta if isinstance(meta, dict) else {}
        self._stamp = stamp
        self._loaded_at = time.time()
        return True

    @property
    def ready(self) -> bool:
        return self._ensure_loaded()

    def built_at(self) -> str:
        return str(self._meta.get("built_at", "")) if self._ensure_loaded() else ""

    # -------------------------------------------------- 检索

    @staticmethod
    def _normalize(query: str) -> str:
        q = re.sub(r"\s+", "", (query or "").strip())
        while True:
            stripped = _LOCAL_SUFFIX.sub("", q)
            if stripped == q or not stripped:
                break
            q = stripped
        return q or (query or "").strip()

    def _load_item(self, subdir: str, item_id: str) -> Optional[dict[str, Any]]:
        aka_dir = self._dir()
        safe = _safe_id(item_id)
        if not aka_dir or not safe:
            return None
        data = _read_json(aka_dir / subdir / f"{safe}.json", None)
        return data if isinstance(data, dict) else None

    def resolve_character(self, query: str) -> Optional[str]:
        """中文/英文名 → 角色 ID。"""
        if not self._ensure_loaded():
            return None
        q = self._normalize(query)
        if not q:
            return None
        hit = self._zh2id.get(q)
        if hit:
            return str(hit)

        # 按中文名长度降序匹配，避免短名抢占「零号·安比」等长名；禁止 1 字泛化子串误命中
        sorted_zh = sorted(self._zh2id.items(), key=lambda kv: len(kv[0]), reverse=True)
        for zh, cid in sorted_zh:
            if len(zh) >= 2 and (zh in q or (len(q) >= 2 and q in zh)):
                return str(cid)
            if len(zh) == 1 and q.startswith(zh):
                return str(cid)

        ql = q.lower()
        for entry in self._char_index:
            name = str(entry.get("name", "")).lower()
            if name == ql:
                return str(entry["id"])
        if len(ql) >= 4:
            for entry in self._char_index:
                name = str(entry.get("name", "")).lower()
                if name and (name.startswith(ql) or ql.startswith(name)):
                    return str(entry["id"])
        return None

    def resolve_weapon(self, query: str) -> Optional[str]:
        if not self._ensure_loaded():
            return None
        q = _name_key(self._normalize(query))
        if not q:
            return None
        hit = self._weapon_names.get(q)
        if hit:
            return hit
        has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in q)
        if len(q) < (2 if has_cjk else 4):
            return None
        matches = [wid for key, wid in self._weapon_names.items() if q in key or key in q]
        return matches[0] if len(matches) == 1 else None

    # -------------------------------------------------- 摘要生成

    def build_guide(self, cid: str, max_chars: int = 1800) -> Optional[str]:
        """米游社养成攻略摘录（crawl_miyoushe_build.py 写入的 build/<id>.json 正文）。"""
        rec = self._load_item("build", cid)
        if not rec:
            return None
        text = str(rec.get("text") or "").strip()
        if not text:
            return None
        name = str(rec.get("zh_name") or "")
        head = f"{name}·养成攻略（米游社）\n{rec.get('title', '')}\n{rec.get('url', '')}\n"
        return head + _trim(text, max(200, max_chars - len(head)))

    def lookup(self, query: str, max_chars: int = 1800) -> Optional[str]:
        """总入口：养成类问题优先给米游社攻略，其次角色/音擎数据。"""
        if not self._ensure_loaded():
            return None
        cid = self.resolve_character(query)
        if cid:
            if any(hint in (query or "") for hint in _BUILD_HINTS):
                guide = self.build_guide(cid, max_chars)
                if guide:
                    return guide
            obj = self._load_item("char", cid)
            if obj:
                return self.build_char_summary(obj, max_chars)
        wid = self.resolve_weapon(query)
        if wid:
            obj = self._load_item("weapon", wid)
            if obj:
                return self.build_weapon_summary(obj, max_chars)
        return None

    def _format_skills(self, obj: dict[str, Any]) -> list[str]:
        skills = obj.get("skills") or {}
        sections: list[str] = []
        for gkey, gname in _SKILL_GROUPS_ZH:
            grp = skills.get(gkey)
            if not isinstance(grp, dict):
                continue
            lines = []
            for sk in (grp.get("skills") or [])[:3]:
                if not isinstance(sk, dict):
                    continue
                sname = _clean(sk.get("zh_name")) or _clean(sk.get("name"))
                sdesc = _clean(sk.get("description"))
                param_str = self._params_str(sk.get("params"))
                if sdesc and param_str:
                    body = f"{_trim(sdesc, 130)}（满级 {param_str}）"
                elif param_str:
                    body = f"满级 {param_str}"
                else:
                    body = sdesc
                lines.append(f"· {sname}: {_trim(body, 260)}" if body else f"· {sname}")
            if lines:
                sections.append(f"【{gname}】\n" + "\n".join(lines))
        return sections

    def _format_core_skills(self, obj: dict[str, Any], zb: dict[str, Any]) -> str:
        passive = obj.get("passiveSkill") or {}
        if not isinstance(passive, dict):
            return ""
        core_lines = []
        for core in (passive.get("core") or [])[:2]:
            if not isinstance(core, dict):
                continue
            cd = _clean(core.get("description"))
            cp = self._params_str(core.get("descriptionParams") if "descriptionParams" in core else core.get("params"))
            body = _trim(f"{cd}（{cp}）" if cp and cd else (cd or cp or ""), 200)
            label = f"核心被动「{zb['core']}」" if zb.get("core") else _clean(core.get("name"))
            if body:
                core_lines.append(f"· {label}: {body}")
        for psv in (passive.get("passives") or [])[:2]:
            if not isinstance(psv, dict):
                continue
            pd = _clean(psv.get("description"))
            label = f"额外能力「{zb['extra']}」" if zb.get("extra") else _clean(psv.get("name"))
            if pd:
                core_lines.append(f"· {label}: {_trim(pd, 140)}")
        return ("【核心技】\n" + "\n".join(core_lines)) if core_lines else ""

    def build_char_summary(self, obj: dict[str, Any], max_chars: int = 1800) -> str:
        zb = obj.get("zh") or {}
        en_name = obj.get("name", "?")
        name = zb.get("name") or en_name
        rarity = obj.get("rarity", "?")
        raw_element = (obj.get("element") or {}).get("text", "")
        raw_camp = (obj.get("camp") or {}).get("text", "")
        element = zb.get("element") or ELEMENT_ZH.get(raw_element, raw_element)
        camp = zb.get("camp") or FACTION_ZH.get(raw_camp, raw_camp)
        specialty = zb.get("specialty") or ""
        beta = "（未实装/情报角色）" if obj.get("beta") else ""

        head = f"{name}" + (f"（{en_name}）" if en_name and en_name != name else "")
        head += f" ★{rarity} | {element} | {camp}"
        if specialty:
            head += f" | {specialty}"
        head += beta
        parts = [head]

        desc = _clean(zb.get("description")) or _clean(obj.get("description"))
        if desc:
            parts.append(_trim(desc, 150))

        parts.extend(self._format_skills(obj))
        core_block = self._format_core_skills(obj, zb)
        if core_block:
            parts.append(core_block)

        dupes = obj.get("dupes") or []
        if dupes:
            dlines = [
                f"{d.get('number')}.{_clean(d.get('zh_name')) or _clean(d.get('name'))}: "
                f"{_trim(_clean(d.get('description')), 90)}"
                for d in dupes[:6]
                if isinstance(d, dict)
            ]
            if dlines:
                parts.append("【影画】\n" + "\n".join(dlines))

        stats_block = self._stats_str(obj)
        if stats_block:
            parts.append(stats_block)

        text = "\n".join(parts)
        return text if len(text) <= max_chars else text[: max_chars - 1] + "…"

    def build_weapon_summary(self, obj: dict[str, Any], max_chars: int = 1800) -> str:
        zb = obj.get("zh") or {}
        en_name = obj.get("name", "?")
        name = zb.get("name") or en_name
        rarity = obj.get("rarity", "?")
        beta = "（未实装/情报）" if obj.get("beta") else ""
        parts = [
            f"{name}" + (f"（{en_name}）" if en_name and en_name != name else "") + f" ★{rarity}（音擎）{beta}"
        ]
        desc = obj.get("description", "")
        if desc and not desc.startswith("Item_"):
            parts.append(_trim(_clean(desc), 200))

        skill = obj.get("skill")
        if zb.get("skill"):
            effect = _trim(_clean(zb.get("effect")), 220)
            parts.append(f"【音擎技能】\n· {zb['skill']}" + (f"：{effect}" if effect else ""))
        elif isinstance(skill, dict):
            sdesc = _clean(skill.get("description"))
            sp = self._params_str(skill.get("params"))
            if sdesc or sp:
                body = f"{_trim(sdesc, 220)}（满级 {sp}）" if sdesc and sp else (sdesc or sp)
                parts.append(f"【音擎技能】\n· {body}")
        elif isinstance(skill, list):
            for entry in skill[:2]:
                if not isinstance(entry, dict):
                    continue
                sdesc = _clean(entry.get("description"))
                sp = self._params_str(entry.get("params"))
                if sdesc or sp:
                    parts.append(f"【音擎技能】\n· {_trim(sdesc, 220)}" + (f"（满级 {sp}）" if sp else ""))

        weapon_stats = obj.get("weapon")
        if isinstance(weapon_stats, dict):
            base = weapon_stats.get("base") or weapon_stats.get("stats")
            if isinstance(base, dict):
                items = [
                    f"{STAT_ZH.get(k, k)} {_num(v.get('value', v) if isinstance(v, dict) else v)}"
                    for k, v in list(base.items())[:4]
                ]
                if items:
                    parts.append("【基础面板】" + " | ".join(items))
        text = "\n".join(parts)
        return text if len(text) <= max_chars else text[: max_chars - 1] + "…"

    # -------------------------------------------------- 内部小件

    @staticmethod
    def _params_str(params: Any, max_items: int = 4) -> str:
        if not isinstance(params, list):
            return ""
        out = []
        for param in params[:max_items]:
            if not isinstance(param, dict):
                continue
            pname = _clean(param.get("name"))
            values = param.get("advancedValues") or param.get("values")
            last = None
            if isinstance(values, list) and values:
                last = values[-1]
                if isinstance(last, dict):
                    last = last.get("value", last)
            if isinstance(last, list):
                items = [_num(x) for x in last if isinstance(x, (int, float, str))]
                last = "/".join(items[:6]) if items else ""
            if pname and last not in (None, ""):
                out.append(f"{pname} {last}")
        return "，".join(out)

    def _stats_str(self, obj: dict[str, Any]) -> str:
        asc = obj.get("ascension") or {}
        if not isinstance(asc, dict):
            return ""
        seg: list[str] = []
        base = asc.get("baseStats") or {}
        if isinstance(base, dict):
            items = [
                f"{STAT_ZH.get(k, k)} {_num(v.get('value') if isinstance(v, dict) else v)}"
                for k, v in list(base.items())[:6]
            ]
            if items:
                seg.append("【基础面板（1级）】" + " | ".join(items))
        levels = asc.get("levels") or []
        if isinstance(levels, list) and levels:
            last_lv = levels[-1] if isinstance(levels[-1], dict) else {}
            last_stats = last_lv.get("stats") or {}
            if isinstance(last_stats, dict) and last_stats:
                items = [f"{STAT_ZH.get(k, k)} {_num(v)}" for k, v in list(last_stats.items())[:3]]
                lv = last_lv.get("maxLevel", "?")
                if items:
                    seg.append(f"【{lv}级面板】" + " | ".join(items))
            mats = last_lv.get("cost") or []
            if isinstance(mats, list) and mats:
                names = "、".join(
                    f"{_clean(m.get('name'))}×{m.get('quantity')}"
                    for m in mats[:4]
                    if isinstance(m, dict) and m.get("name")
                )
                if names:
                    seg.append(f"【升阶材料（终阶）】{names}")
        return "\n".join(seg)
